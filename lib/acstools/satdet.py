"""This module contains the tools needed for satellite detection
within an ACS/WFC image as published in
`ACS ISR 2016-01 <http://www.stsci.edu/hst/acs/documents/isrs/isr1601.pdf>`_.

.. note::

    Only tested for ACS/WFC FLT and FLC images, but it should
    theoretically work for any instrument.

    :func:`skimage.transform.probabilistic_hough_line` gives
    slightly different results from run to run, but this should
    not matter since :func:`detsat` only provides crude
    approximation of the actual trail(s).

    Performance is faster when ``plot=False``, where applicable.

    Currently *not* supported in TEAL and PyRAF.

Examples
--------
>>> from acstools.satdet import detsat, make_mask, update_dq

Find trail segments for a single image and extension without multiprocessing,
and display plots (not shown) and verbose information:

>>> results, errors = detsat(
...     'jc8m10syq_flc.fits', chips=[4], n_processes=1, plot=True, verbose=True)
1 file(s) found...
Processing jc8m10syq_flc.fits[4]...
Rescale intensity percentiles: 110.161376953, 173.693756409
Length of PHT result: 42
min(x0)=   1, min(x1)= 269, min(y0)= 827, min(y1)= 780
max(x0)=3852, max(x1)=4094, max(y0)=1611, max(y1)=1545
buf=200
topx=3896, topy=1848
...
Trail Direction: Right to Left
42 trail segment(s) detected
...
End point list:
    1. (1256, 1345), (2770, 1037)
    2. (  11, 1598), ( 269, 1545)
...
Total run time: 22.4326269627 s
>>> results[('jc8m10syq_flc.fits', 4)]
array([[[1242, 1348],
        [2840, 1023]],
       [[1272, 1341],
        [2688, 1053]],
       ...
       [[2697, 1055],
        [2967, 1000]]])
>>> errors
{}

Find trail segments for multiple images and all ACS/WFC science extensions with
multiprocessing:

>>> results, errors = detsat(
...     '*_flc.fits', chips=[1, 4], n_processes=12, verbose=True)
6 file(s) found...
Using 12 processes
Number of trail segment(s) found:
  abell2744-hffpar_acs-wfc_f814w_13495_11_01_jc8n11q9q_flc.fits[1]: 0
  abell2744-hffpar_acs-wfc_f814w_13495_11_01_jc8n11q9q_flc.fits[4]: 4
  abell2744_acs-wfc_f814w_13495_51_04_jc8n51hoq_flc.fits[1]: 2
  abell2744_acs-wfc_f814w_13495_51_04_jc8n51hoq_flc.fits[4]: 34
  abell2744_acs-wfc_f814w_13495_93_02_jc8n93a7q_flc.fits[1]: 20
  abell2744_acs-wfc_f814w_13495_93_02_jc8n93a7q_flc.fits[4]: 20
  j8oc01sxq_flc.fits[1]: 0
  j8oc01sxq_flc.fits[4]: 0
  jc8m10syq_flc.fits[1]: 0
  jc8m10syq_flc.fits[4]: 38
  jc8m32j5q_flc.fits[1]: 42
  jc8m32j5q_flc.fits[4]: 12
Total run time: 34.6021330357 s
>>> results[('jc8m10syq_flc.fits', 4)]
array([[[1242, 1348],
        [2840, 1023]],
       [[1272, 1341],
        [2688, 1053]],
       ...
       [[2697, 1055],
        [2967, 1000]]])
>>> errors
{}

For a given image and extension, create a DQ mask for a satellite trail using
the first segment (other segments should give similar masks) based on the
results from above (plots not shown):

>>> trail_coords = results[('jc8m10syq_flc.fits', 4)]
>>> trail_segment = trail_coords[0]
>>> trail_segment
array([[1199, 1357],
       [2841, 1023]])
>>> mask = make_mask('jc8m10syq_flc.fits', 4, trail_segment,
...                  plot=True, verbose=True)
Rotation: -11.4976988695
Hit image edge at counter=26
Hit rotate edge at counter=38
Run time: 19.476323843 s

Update the corresponding DQ array using the mask from above:

>>> update_dq('jc8m10syq_flc.fits', 6, mask, verbose=True)
DQ flag value is 16384
Input... flagged NPIX=156362
Existing flagged NPIX=0
Newly... flagged NPIX=156362
jc8m10syq_flc.fits[6] updated

"""
#
# History:
#    Dec 12, 2014 - DMB - Created for COSC 602 "Image Processing and Pattern
#        Recocnition" at Towson University. Mostly detection algorithm
#        development and VERY crude mask.
#    Feb 01, 2015 - DMB - Masking algorithm refined to be useable by HFF.
#    Mar 28, 2015 - DMB - Small bug fixes and tweaking to try not to include
#        diffraction spikes.
#    Nov 03, 2015 - PLL - Adapted for acstools distribution. Fixed bugs,
#        possibly improved performance, changed API.
#    Dec 07, 2015 - PLL - Minor changes based on feedback from DMB.
#
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
from astropy.extern.six.moves import map

# STDLIB
import glob
import multiprocessing
import time
import warnings
from multiprocessing import Process, Queue

# THIRD PARTY
import numpy as np
from astropy.io import fits
#from astropy.stats import biweight_location
from astropy.stats import biweight_midvariance, sigma_clipped_stats
from astropy.utils.exceptions import AstropyUserWarning
from scipy import stats
from skimage import filter as filt
from skimage import transform
from skimage import morphology as morph
from skimage import exposure

try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None
    warnings.warn('matplotlib not found, plotting is disabled',
                  AstropyUserWarning)

__version__ = '0.3.1'
__vdate__ = '25-Apr-2016'
__author__ = 'David Borncamp, Pey Lian Lim'
__all__ = ['detsat', 'make_mask', 'update_dq']


############################## from satdet.py ##################################

def _detsat_one(filename, ext, sigma=2.0, low_thresh=0.1, h_thresh=0.5,
                small_edge=60, line_len=200, line_gap=75,
                percentile=(4.5, 93.0), buf=200, plot=False, verbose=False):
    """Called by :func:`detsat`."""
    if verbose:
        t_beg = time.time()

    fname = '{0}[{1}]'.format(filename, ext)

    # check extension
    if ext not in (1, 4, 'SCI', ('SCI', 1), ('SCI', 2)):
        warnings.warn('{0} is not a valid science extension for '
                      'ACS/WFC'.format(ext), AstropyUserWarning)

    # get the data
    image = fits.getdata(filename, ext)
    #image = im.astype('float64')

    # rescale the image
    p1, p2 = np.percentile(image, percentile)

    # there should always be some counts in the image, anything lower should
    # be set to one. Makes things nicer for finding edges.
    if p1 < 0:
        p1 = 0.0

    if verbose:
        print('Rescale intensity percentiles: {0}, {1}'.format(p1, p2))

    image = exposure.rescale_intensity(image, in_range=(p1, p2))

    # get the edges
    immax = np.max(image)
    edge = filt.canny(image, sigma=sigma,
                      low_threshold=immax * low_thresh,
                      high_threshold=immax * h_thresh)

    # clean up the small objects, will make less noise
    morph.remove_small_objects(edge, min_size=small_edge, connectivity=8,
                               in_place=True)

    # create an array of angles from 0 to 180, exactly 0 will get bad columns
    # but it is unlikely that a satellite will be exactly at 0 degrees, so
    # don't bother checking.
    # then, convert to radians.
    angle = np.radians(np.arange(2, 178, 0.5, dtype=float))

    # perform Hough Transform to detect straight lines.
    # only do if plotting to visualize the image in hough space.
    # otherwise just preform a Probabilistic Hough Transform.
    if plot and plt is not None:
        h, theta, d = transform.hough_line(edge, theta=angle)
        plt.ion()

    # perform Probabilistic Hough Transformation to get line segments.
    # NOTE: Results are slightly different from run to run!
    result = transform.probabilistic_hough_line(
        edge, threshold=210, line_length=line_len,
        line_gap=line_gap, theta=angle)
    result = np.asarray(result)
    n_result = len(result)

    # initially assume there is no satellite
    satellite = False

    # only continue if there was more than one point (at least a line)
    # returned from the PHT
    if n_result > 1:
        if verbose:
            print('Length of PHT result: {0}'.format(n_result))

        # create lists for X and Y positions of lines and build points
        x0 = result[:, 0, 0]
        y0 = result[:, 0, 1]
        x1 = result[:, 1, 0]
        y1 = result[:, 1, 1]

        # set some boundries
        ymax, xmax = image.shape
        topx = xmax - buf
        topy = ymax - buf

        if verbose:
            print('min(x0)={0:4d}, min(x1)={1:4d}, min(y0)={2:4d}, '
                  'min(y1)={3:4d}'.format(min(x0), min(x1), min(y0), min(y1)))
            print('max(x0)={0:4d}, max(x1)={1:4d}, max(y0)={2:4d}, '
                  'max(y1)={3:4d}'.format(max(x0), max(x1), max(y0), max(y1)))
            print('buf={0}'.format(buf))
            print('topx={0}, topy={1}'.format(topx, topy))

        # set up trail angle "tracking" arrays.
        # find the angle of each segment and filter things out.
        # TODO: this may be wrong. Try using arctan2.
        trail_angle = np.degrees(np.arctan((y1 - y0) / (x1 - x0)))
        # round to the nearest 5 degrees, trail should not be that curved
        round_angle = (5 * np.round(trail_angle * 0.2)).astype(int)

        # take out 90 degree things
        mask = round_angle % 90 != 0

        if not np.any(mask):
            if verbose:
                print('No round_angle found')
            return np.empty(0)

        round_angle = round_angle[mask]
        trail_angle = trail_angle[mask]
        result = result[mask]

        ang, num = stats.mode(round_angle)

        # do the filtering
        truth = round_angle == ang[0]

        if verbose:
            print('trail_angle: {0}'.format(trail_angle))
            print('round_angle: {0}'.format(round_angle))
            print('mode(round_angle): {0}'.format(ang[0]))

        # filter out the outliers
        trail_angle = trail_angle[truth]
        result = result[truth]
        n_result = len(result)

        if verbose:
            print('Filtered trail_angle: {0}'.format(trail_angle))

        if n_result < 1:
            return np.empty(0)

        # if there is an unreasonable amount of points, it picked up garbage
        elif n_result > 300:
            warnings.warn(
                'Way too many segments results to be correct ({0}). Rejecting '
                'detection on {1}.'.format(n_result, fname), AstropyUserWarning)
            return np.empty(0)

        # remake the point lists with things taken out
        x0 = result[:, 0, 0]
        y0 = result[:, 0, 1]
        x1 = result[:, 1, 0]
        y1 = result[:, 1, 1]

        min_x0 = min(x0)
        min_y0 = min(y0)
        min_x1 = min(x1)
        min_y1 = min(y1)

        max_x0 = max(x0)
        max_y0 = max(y0)
        max_x1 = max(x1)
        max_y1 = max(y1)

        mean_angle = np.mean(trail_angle)

        # make decisions on where the trail went and determine if a trail
        # traversed the image
        # top to bottom
        if (((min_y0 < buf) or (min_y1 < buf)) and
              ((max_y0 > topy) or (max_y1 > topy))):
            satellite = True
            if verbose:
                print('Trail Direction: Top to Bottom')

        # right to left
        elif (((min_x0 < buf) or (min_x1 < buf)) and
              ((max_x0 > topx) or (max_x1 > topx))):
            satellite = True
            if verbose:
                print('Trail Direction: Right to Left')

        # bottom to left
        elif (((min_x0 < buf) or (min_x1 < buf)) and
              ((min_y0 < buf) or (min_y1 < buf)) and
              (-1 > mean_angle > -89)):
            satellite = True
            if verbose:
                print('Trail Direction: Bottom to Left')

        # top to left
        elif (((min_x0 < buf) or (min_x1 < buf)) and
              ((max_y0 > topy) or (max_y1 > topy)) and
              (89 > mean_angle > 1)):
            satellite = True
            if verbose:
                print('Trail Direction: Top to Left')

        # top to right
        elif (((max_x0 > topx) or (max_x1 > topx)) and
              ((max_y0 > topy) or (max_y1 > topy)) and
              (-1 > mean_angle > -89)):
            satellite = True
            if verbose:
                print('Trail Direction: Top to Right')

        # bottom to right
        elif (((max_x0 > topx) or (max_x1 > topx)) and
              ((min_y0 < buf) or (min_y1 < buf)) and
              (89 > mean_angle > 1)):
            satellite = True
            if verbose:
                print('Trail Direction: Bottom to Right')

    if satellite:
        if verbose:
            print('{0} trail segment(s) detected'.format(n_result))
            print('Trail angle list (not returned): ')
            print(trail_angle)
            print('End point list:')
            for i, ((px0, py0), (px1, py1)) in enumerate(result, 1):
                print('{0:5d}. ({1:4d}, {2:4d}), ({3:4d}, {4:4d})'.format(
                    i, px0, py0, px1, py1))

        if plot and plt is not None:
            mean = np.median(image)
            stddev = image.std()
            lower = mean - stddev
            upper = mean + stddev

            fig1, ax1 = plt.subplots()
            ax1.imshow(edge, cmap=plt.cm.gray)
            ax1.set_title('Edge image for {0}'.format(fname))

            for (px0, py0), (px1, py1) in result:  # Draw trails
                ax1.plot((px0, px1), (py0, py1), scalex=False, scaley=False)

            fig2, ax2 = plt.subplots()
            ax2.imshow(
                np.log(1 + h),
                extent=(np.rad2deg(theta[-1]), np.rad2deg(theta[0]),
                        d[-1], d[0]), aspect=0.02)
            ax2.set_title('Hough Transform')
            ax2.set_xlabel('Angles (degrees)')
            ax2.set_ylabel('Distance from Origin (pixels)')

            fig3, ax3 = plt.subplots()
            ax3.imshow(image, vmin=lower, vmax=upper, cmap=plt.cm.gray)
            ax3.set_title(fname)

            for (px0, py0), (px1, py1) in result:  # Draw trails
                ax3.plot((px0, px1), (py0, py1), scalex=False, scaley=False)

            plt.draw()

    else:  # length of result was too small
        result = np.empty(0)

        if verbose:
            print('No trail detected; found {0} segments'.format(n_result))

        if plot and plt is not None:
            fig1, ax1 = plt.subplots()
            ax1.imshow(edge, cmap=plt.cm.gray)
            ax1.set_title(fname)

            # Draw trails
            for (px0, py0), (px1, py1) in result:
                ax1.plot((px0, px1), (py0, py1), scalex=False, scaley=False)

    if verbose:
        t_end = time.time()
        print('Run time: {0} s'.format(t_end - t_beg))

    return result


def _get_valid_indices(shape, ix0, ix1, iy0, iy1):
    """Give array shape and desired indices, return indices that are
    correctly bounded by the shape."""
    ymax, xmax = shape

    if ix0 < 0:
        ix0 = 0
    if ix1 > xmax:
        ix1 = xmax
    if iy0 < 0:
        iy0 = 0
    if iy1 > ymax:
        iy1 = ymax

    if iy1 <= iy0 or ix1 <= ix0:
        raise IndexError(
            'array[{0}:{1},{2}:{3}] is invalid'.format(iy0, iy1, ix0, ix1))

    return list(map(int, [ix0, ix1, iy0, iy1]))


def _rotate_point(point, angle, ishape, rshape, reverse=False):
    """Transform a point from original image coordinates to rotated image
    coordinates and back. It assumes the rotation point is the center of an
    image.

    This works on a simple rotation transformation::

        newx = (startx) * np.cos(angle) - (starty) * np.sin(angle)
        newy = (startx) * np.sin(angle) + (starty) * np.cos(angle)

    It takes into account the differences in image size.

    Parameters
    ----------
    point : tuple
        Point to be rotated, in the format of ``(x, y)`` measured from
        origin.

    angle : float
        The angle in degrees to rotate the point by as measured
        counter-clockwise from the X axis.

    ishape : tuple
        The shape of the original image, taken from ``image.shape``.

    rshape : tuple
        The shape of the rotated image, in the form of ``rotate.shape``.

    reverse : bool, optional
        Transform from rotated coordinates back to non-rotated image.

    Returns
    -------
    rotated_point : tuple
        Rotated point in the format of ``(x, y)`` as measured from origin.

    """
    #  unpack the image and rotated images shapes
    if reverse:
        angle = (angle * -1)
        temp = ishape
        ishape = rshape
        rshape = temp

    # transform into center of image coordinates
    yhalf, xhalf = ishape
    yrhalf, xrhalf = rshape

    yhalf = yhalf / 2
    xhalf = xhalf / 2
    yrhalf = yrhalf / 2
    xrhalf = xrhalf / 2

    startx = point[0] - xhalf
    starty = point[1] - yhalf

    # do the rotation
    newx = startx * np.cos(angle) - starty * np.sin(angle)
    newy = startx * np.sin(angle) + starty * np.cos(angle)

    # add back the padding from changing the size of the image
    newx = newx + xrhalf
    newy = newy + yrhalf

    return (newx, newy)


def make_mask(filename, ext, trail_coords, sublen=75, subwidth=200, order=3,
              sigma=4, pad=10, plot=False, verbose=False):
    """Create DQ mask for an image for a given satellite trail.
    This mask can be added to existing DQ data using :func:`update_dq`.

    .. note::

        Unlike :func:`detsat`, multiprocessing is not available for
        this function.

    Parameters
    ----------
    filename : str
        FITS image filename.

    ext : int, str, or tuple
        Extension for science data, as accepted by ``astropy.io.fits``.

    trail_coords : ndarray
        One of the trails returned by :func:`detsat`.
        This must be in the format of ``[[x0, y0], [x1, y1]]``.

    sublen : int, optional
        Length of strip to use as the fitting window for the trail.

    subwidth : int, optional
        Width of box to fit trail on.

    order : int, optional
        The order of the spline interpolation for image rotation.
        See :func:`skimage.transform.rotate`.

    sigma : float, optional
        Sigma of the satellite trail for detection. If points are
        a given sigma above the background in the subregion then it is
        marked as a satellite. This may need to be lowered for resolved
        trails.

    pad : int, optional
        Amount of extra padding in pixels to give the satellite mask.

    plot : bool, optional
        Plot the result.

    verbose : bool, optional
        Print extra information to the terminal, mostly for debugging.

    Returns
    -------
    mask : ndarray
        Boolean array marking the satellite trail with `True`.

    Raises
    ------
    IndexError
        Invalid subarray indices.

    ValueError
        Image has no positive values, trail subarray too small, or
        trail profile not found.

    """
    if verbose:
        t_beg = time.time()

    fname = '{0}[{1}]'.format(filename, ext)
    image = fits.getdata(filename, ext)

    dx = image.max()
    if dx <= 0:
        raise ValueError('Image has no positive values')

    # rescale the image
    image = image / dx
    # make sure everything is at least 0
    image[image < 0] = 0

    (x0, y0), (x1, y1) = trail_coords  # p0, p1

    #  Find out how much to rotate the image
    rad = np.arctan2(y1 - y0, x1 - x0)
    newrad = (np.pi * 2) - rad
    deg = np.degrees(rad)

    if verbose:
        print('Rotation: {0}'.format(deg))

    rotate = transform.rotate(image, deg, resize=True, order=order)

    if plot and plt is not None:
        plt.ion()
        mean = np.median(image)
        stddev = image.std()
        lower = mean - stddev
        upper = mean + stddev

        fig1, ax1 = plt.subplots()
        ax1.imshow(image, vmin=lower, vmax=upper, cmap=plt.cm.gray)
        ax1.set_title(fname)

        fig2, ax2 = plt.subplots()
        ax2.imshow(rotate, vmin=lower, vmax=upper, cmap=plt.cm.gray)
        ax2.set_title('{0} rotated by {1} deg'.format(fname, deg))

        plt.draw()

    #  Will do all of this in the loop, but want to make sure there is a
    #  good point first and that there is indeed a profile to fit.
    #  get starting point
    sx, sy = _rotate_point((x0, y0), newrad, image.shape, rotate.shape)

    #  start with one subarray around p0
    dx = int(subwidth / 2)
    ix0, ix1, iy0, iy1 = _get_valid_indices(
        rotate.shape, sx - dx, sx + dx, sy - sublen, sy + sublen)
    subr = rotate[iy0:iy1, ix0:ix1]
    if len(subr) <= sublen:
        raise ValueError('Trail subarray size is {0} but expected {1} or '
                         'larger'.format(len(subr), sublen))

    # Flatten the array so we are looking along rows
    # Take median of each row, should filter out most outliers
    # This list will get appended in the loop
    medarr = np.median(subr, axis=1)
    flat = [medarr]

    # get the outliers
    #mean = biweight_location(medarr)
    mean = sigma_clipped_stats(medarr)[0]
    stddev = biweight_midvariance(medarr)

    # only flag things that are sigma from the mean
    z = np.where(medarr > (mean + (sigma * stddev)))[0]

    if plot and plt is not None:
        fig1, ax1 = plt.subplots()
        ax1.plot(medarr, 'b.')
        ax1.plot(z, medarr[z], 'r.')
        ax1.set_xlabel('Index')
        ax1.set_ylabel('Value')
        ax1.set_title('Median array in flat[0]')
        plt.draw()

    # Make sure there is something in the first pass before trying to move on
    if len(z) < 1:
        raise ValueError(
            'First look at finding a profile failed. '
            'Nothing found at {0} from background! '
            'Adjust parameters and try again.'.format(sigma))

    # get the bounds of the flagged points
    lower = z.min()
    upper = z.max()
    diff = upper - lower

    # add in a pading value to make sure all of the wings are accounted for
    lower = lower - pad
    upper = upper + pad

    # for plotting see how the profile was made (append to plot above)
    if plot and plt is not None:
        padind = np.arange(lower, upper)
        ax1.plot(padind, medarr[padind], 'yx')
        plt.draw()

    # start to create a mask
    mask = np.zeros(rotate.shape)
    lowerx, upperx, lowery, uppery  = _get_valid_indices(
        mask.shape, np.floor(sx - subwidth), np.ceil(sx + subwidth),
        np.floor(sy - sublen + lower), np.ceil(sy - sublen + upper))
    mask[lowery:uppery, lowerx:upperx] = 1

    done = False
    first = True
    nextx = upperx  # np.ceil(sx + subwidth)
    centery = np.ceil(lowery + diff)  # np.ceil(sy - sublen + lower + diff)
    counter = 0

    while not done:
        # move to the right of the centerpoint first. do the same
        # as above but keep moving right until the edge is hit.
        ix0, ix1, iy0, iy1 = _get_valid_indices(
            rotate.shape, nextx - dx, nextx + dx,
            centery - sublen, centery + sublen)
        subr = rotate[iy0:iy1, ix0:ix1]

        # determines the edge, if the subr is not good, then the edge was
        # hit.
        if 0 in subr.shape:
            if verbose:
                print('Hit edge, subr shape={0}, first={1}'.format(
                    subr.shape, first))
            if first:
                first = False
                centery = sy
                nextx = sx
            else:
                done = True
            continue

        medarr = np.median(subr, axis=1)
        flat.append(medarr)

        #mean = biweight_location(medarr)
        mean = sigma_clipped_stats(medarr, sigma=sigma)[0]
        stddev = biweight_midvariance(medarr)  # Might give RuntimeWarning
        z = np.where(medarr > (mean + (sigma * stddev)))[0]

        if len(z) < 1:
            if first:
                if verbose:
                    print('No good profile found for counter={0}. Start '
                          'moving left from starting point.'.format(counter))
                centery = sy
                nextx = sx
                first = False
            else:
                if verbose:
                    print('z={0} is less than 1, subr shape={1}, '
                          'we are done'.format(z, subr.shape))
                done = True
            continue

        # get the bounds of the flagged points
        lower = z.min()
        upper = z.max()
        diff = upper - lower

        # add in a pading value to make sure all of the wings
        # are accounted for
        lower = np.floor(lower - pad)
        upper = np.ceil(upper + pad)
        lowerx, upperx, lowery, uppery  = _get_valid_indices(
            mask.shape,
            np.floor(nextx - subwidth),
            np.ceil(nextx + subwidth),
            np.floor(centery - sublen + lower),
            np.ceil(centery - sublen + upper))
        mask[lowery:uppery, lowerx:upperx] = 1

        lower_p = (lowerx, lowery)
        upper_p = (upperx, uppery)
        lower_t = _rotate_point(
            lower_p, newrad, image.shape, rotate.shape, reverse=True)
        upper_t = _rotate_point(
            upper_p, newrad, image.shape, rotate.shape, reverse=True)

        lowy = np.floor(lower_t[1])
        highy = np.ceil(upper_t[1])
        lowx = np.floor(lower_t[0])
        highx = np.ceil(upper_t[0])

        # Reset the next subr to be at the center of the profile
        if first:
            nextx = nextx + dx
            centery = lowery + diff  # centery - sublen + lower + diff

            if (nextx + subwidth) > rotate.shape[1]:
                if verbose:
                    print('Hit rotate edge at counter={0}'.format(counter))
                first = False
            elif (highy > image.shape[0]) or (highx > image.shape[1]):
                if verbose:
                    print('Hit image edge at counter={0}'.format(counter))
                first = False

            if not first:
                centery = sy
                nextx = sx

        # Not first, this is the pass the other way.
        else:
            nextx = nextx - dx
            centery = lowery + diff  # centery - sublen + lower + diff

            if (nextx - subwidth) < 0:
                if verbose:
                    print('Hit rotate edge at counter={0}'.format(counter))
                done = True
            elif (highy > image.shape[0]) or (highx > image.shape[1]):
                if verbose:
                    print('Hit image edge at counter={0}'.format(counter))
                done = True

        counter += 1

        # make sure it does not try to go infinetly
        if counter > 500:
            if verbose:
                print('Too many loops, exiting')
            done = True
    # End while

    rot = transform.rotate(mask, -deg, resize=True, order=1)
    ix0 = (rot.shape[1] - image.shape[1]) / 2
    iy0 = (rot.shape[0] - image.shape[0]) / 2
    lowerx, upperx, lowery, uppery  = _get_valid_indices(
        rot.shape, ix0, image.shape[1] + ix0, iy0, image.shape[0] + iy0)
    mask = rot[lowery:uppery, lowerx:upperx]

    if mask.shape != image.shape:
        warnings.warn('Output mask shape is {0} but input image shape is '
                      '{1}'.format(mask.shape, image.shape), AstropyUserWarning)

    # Change to boolean mask
    mask = mask.astype(np.bool)

    if plot and plt is not None:
        # debugging array
        test = image.copy()
        test[mask] = 0

        mean = np.median(test)
        stddev = test.std()
        lower = mean - stddev
        upper = mean + stddev

        fig1, ax1 = plt.subplots()
        ax1.imshow(test, vmin=lower, vmax=upper, cmap=plt.cm.gray)
        ax1.set_title('Masked image')

        fig2, ax2 = plt.subplots()
        ax2.imshow(mask, cmap=plt.cm.gray)
        ax2.set_title('DQ mask')

        plt.draw()

    if verbose:
        t_end = time.time()
        print('Run time: {0} s'.format(t_end - t_beg))

    return mask


def update_dq(filename, ext, mask, dqval=16384, verbose=True):
    """Update the given image and DQ extension with the given
    satellite trails mask and flag.

    Parameters
    ----------
    filename : str
        FITS image filename to update.

    ext : int, str, or tuple
        DQ extension, as accepted by ``astropy.io.fits``, to update.

    mask : ndarray
        Boolean mask, with `True` marking the satellite trail(s).
        This can be the result(s) from :func:`make_mask`.

    dqval : int, optional
        DQ value to use for the trail. Default value of 16384 is
        tailored for ACS/WFC.

    verbose : bool, optional
        Print extra information to the terminal.

    """
    with fits.open(filename, mode='update') as pf:
        dqarr = pf[ext].data
        old_mask = (dqval & dqarr) != 0  # Existing flagged trails
        new_mask = mask & ~old_mask  # Only flag previously unflagged trails
        npix_updated = np.count_nonzero(new_mask)

        # Update DQ extension only if necessary
        if npix_updated > 0:
            pf[ext].data[new_mask] += dqval
            pf['PRIMARY'].header.add_history('{0} satdet v{1}({2})'.format(
                time.ctime(), __version__, __vdate__))
            pf['PRIMARY'].header.add_history(
                '  Updated {0} px in EXT {1} with DQ={2}'.format(
                    npix_updated, ext, dqval))

    if verbose:
        fname = '{0}[{1}]'.format(filename, ext)

        print('DQ flag value is {0}'.format(dqval))
        print('Input... flagged NPIX={0}'.format(np.count_nonzero(mask)))
        print('Existing flagged NPIX={0}'.format(np.count_nonzero(old_mask)))
        print('Newly... flagged NPIX={0}'.format(npix_updated))

        if npix_updated > 0:
            print('{0} updated'.format(fname))
        else:
            print('No updates necessary for {0}'.format(fname))


############################## from decttest.py ################################
# Multiprocessing for multiple input files.
# This is for detection only, not for mask.

def _satdet_worker(work_queue, done_queue, sigma=2.0, low_thresh=0.1,
                   h_thresh=0.5, small_edge=60, line_len=200, line_gap=75,
                   percentile=(4.5, 93.0), buf=200):
    """Multiprocessing worker."""
    for fil, chip in iter(work_queue.get, 'STOP'):
        try:
            result = _detsat_one(
                fil, chip, sigma=sigma,
                low_thresh=low_thresh, h_thresh=h_thresh, small_edge=small_edge,
                line_len=line_len, line_gap=line_gap,
                percentile=percentile, buf=buf, plot=False, verbose=False)
        except Exception as e:
            retcode = False
            result = '{0}: {1}'.format(type(e), str(e))
        else:
            retcode = True
        done_queue.put((retcode, fil, chip, result))

    return True


def detsat(searchpattern, chips=[1, 4], n_processes=4, sigma=2.0,
           low_thresh=0.1, h_thresh=0.5, small_edge=60, line_len=200,
           line_gap=75, percentile=(4.5, 93.0), buf=200, plot=False,
           verbose=True):
    """Find satellite trails in the given images and extensions.
    The trails are calculated using Probabilistic Hough Transform.

    .. note::

        The trail endpoints found here are crude approximations.
        Use :func:`make_mask` to create the actual DQ mask for the trail(s)
        of interest.

    Parameters
    ----------
    searchpattern : str
        Search pattern for input FITS images, as accepted by
        :py:func:`glob.glob`.

    chips : list
        List of extensions for science data, as accepted by ``astropy.io.fits``.
        The default values of ``[1, 4]`` are tailored for ACS/WFC.

    n_processes : int
        Number of processes for multiprocessing, which is only useful
        if you are processing a lot of images or extensions.
        If 1 is given, no multiprocessing is done.

    sigma : float, optional
        The size of a Gaussian filter to use before edge detection.
        The default is 2, which is good for almost all images.

    low_thresh : float, optional
        The lower threshold for hysteresis linking of edge pieces.
        This should be between 0 and 1, and less than ``h_thresh``.

    h_thresh : float, optional
        The upper threshold for hysteresis linking of edge pieces.
        This should be between 0 and 1, and greater than ``low_thresh``.

    small_edge : int, optional
        Size of perimeter of small objects to remove in edge image.
        This significantly reduces noise before doing Hough Transform.
        If it is set too high, you will remove the edge of the
        satellite you are trying to find.

    line_len : int, optional
        Minimum line length for Probabilistic Hough Transform to fit.

    line_gap : int, optional
        The largest gap in points allowed for the Probabilistic
        Hough Transform.

    percentile : tuple of float, optional
        The percent boundaries to scale the image to before
        creating edge image.

    buf : int, optional
        How close to the edge of the image the satellite trail has to
        be to be considered a trail.

    plot : bool, optional
        Make plots of edge image, Hough space transformation, and
        rescaled image. This is only applicable if ``n_processes=1``.

    verbose : bool, optional
        Print extra information to the terminal, mostly for debugging.
        In multiprocessing mode, info from individual process is not printed.

    Returns
    -------
    results : dict
        Dictionary mapping ``(filename, ext)`` to an array of endpoints of
        line segments in the format of ``[[x0, y0], [x1, y1]]`` (if found) or
        an empty array (if not). These are the segments that have been
        identified as making up part of a satellite trail.

    errors : dict
        Dictionary mapping ``(filename, ext)`` to the error message explaining
        why processing failed.

    """
    if verbose:
        t_beg = time.time()

    files = glob.glob(searchpattern)
    n_files = len(files)
    n_chips = len(chips)
    n_tot = n_files * n_chips
    n_cpu = multiprocessing.cpu_count()
    results = {}
    errors = {}

    if verbose:
        print('{0} file(s) found...'.format(n_files))

    # Nothing to do
    if n_files < 1 or n_chips < 1:
        return results, errors

    # Adjust number of processes
    if n_tot < n_processes:
        n_processes = n_tot
    if n_processes > n_cpu:
        n_processes = n_cpu

    # No multiprocessing
    if n_processes == 1:
        for fil in files:
            for chip in chips:
                if verbose:
                    print('\nProcessing {0}[{1}]...'.format(fil, chip))

                key = (fil, chip)
                try:
                    result = _detsat_one(
                        fil, chip, sigma=sigma,
                        low_thresh=low_thresh, h_thresh=h_thresh,
                        small_edge=small_edge, line_len=line_len,
                        line_gap=line_gap, percentile=percentile, buf=buf,
                        plot=plot, verbose=verbose)
                except Exception as e:
                    errmsg = '{0}: {1}'.format(type(e), str(e))
                    errors[key] = errmsg
                    if verbose:
                        print(errmsg)
                else:
                    results[key] = result
        if verbose:
            print()

    # Multiprocessing.
    # The work queue is for things that need to be done and is shared by all
    # processes. When a worker finishes, its output is put into done queue.
    else:
        if verbose:
            print('Using {0} processes'.format(n_processes))

        work_queue = Queue()
        done_queue = Queue()
        processes = []

        for fil in files:
            for chip in chips:
                work_queue.put((fil, chip))

        for w in range(n_processes):
            p = Process(
                target=_satdet_worker, args=(work_queue, done_queue), kwargs={
                    'sigma': sigma, 'low_thresh': low_thresh,
                    'h_thresh': h_thresh, 'small_edge': small_edge,
                    'line_len': line_len, 'line_gap': line_gap,
                    'percentile': percentile, 'buf': buf})
            p.start()
            processes.append(p)
            work_queue.put('STOP')

        for p in processes:
            p.join()

        done_queue.put('STOP')

        # return a dictionary of lists
        for status in iter(done_queue.get, 'STOP'):
            key = (status[1], status[2])
            if status[0]:  # Success
                results[key] = status[3]
            else:  # Failed
                errors[key] = status[3]

        if verbose:
            if len(results) > 0:
                print('Number of trail segment(s) found:')
            for key in sorted(results):
                print('  {0}[{1}]: {2}'.format(
                    key[0], key[1], len(results[key])))
            if len(errors) > 0:
                print('These have errors:')
            for key in sorted(errors):
                print('  {0}[{1}]'.format(key[0], key[1]))

    if verbose:
        t_end = time.time()
        print('Total run time: {0} s'.format(t_end - t_beg))

    return results, errors
