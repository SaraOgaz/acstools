/* constants describing the CTE parameters reference file */
#define NUM_PHI 9  /* number of phi values in cte params file */
#define NUM_PSI 13  /* number of psi nodes in cte params file (also # of rows in table) */
#define NUM_LOGQ 4  /* number of log q columns in psi array */
#define NUM_LEV 107 /* number of specified Q levels */

/* constants describing the CTE characterization */
#define MAX_TAIL_LEN 60  /* CTE trails are characterized out to 60 pixels */
#define MAX_PHI 99999     /* max number of phi nodes */
#define CTE_REF_ROW 2048  /* row from which CTE is measured */

/* constants for different instrument names */
#define ACSWFC 1

/* parameters of readout noise decomposition routines */
#define NOISE_MODEL 1

/* function prototypes */
double CalcCteFrac(const double mjd, const int instrument);
int InterpolatePsi(const double chg_leak[NUM_PSI*NUM_LOGQ], const int psi_node[],
                   double chg_leak_interp[MAX_TAIL_LEN*NUM_LOGQ],
                   double chg_open_interp[MAX_TAIL_LEN*NUM_LOGQ]);
int InterpolatePhi(const double dtde_l[NUM_PHI], const int q_dtde[NUM_PHI],
                   const int shft_nit, double dtde_q[MAX_PHI]);
int FillLevelArrays(const double chg_leak_kt[MAX_TAIL_LEN*NUM_LOGQ],
                    const double chg_open_kt[MAX_TAIL_LEN*NUM_LOGQ],
                    const double dtde_q[MAX_PHI], const int levels[NUM_LEV],
                    double chg_leak_lt[MAX_TAIL_LEN*NUM_LEV],
                    double chg_open_lt[MAX_TAIL_LEN*NUM_LEV],
                    double dpde_l[NUM_LEV],
                    int tail_len[NUM_LEV]);
int DecomposeRN(const int arrx, const int arry, const double data[arrx*arry], 
                const int model, const int nitr, const double readnoise,
                double sig_arr[arrx*arry], double noise_arr[arrx*arry]);
int DecomposeRNModel1(const int arrx, const int arry, 
                      const double data[arrx*arry], const int nitr,
                      double sig[arrx*arry], double noise[arrx*arry]);
int DecomposeRNModel100(const int arrx, const int arry, const double readnoise,
                        const double data[arrx*arry],
                        double sig[arrx*arry], double noise[arrx*arry]);
int FixYCte(const int arrx, const int arry, const double sig_cte[arrx*arry],
            double sig_cor[arrx*arry], const double cte_frac, const int sim_nit,
            const int shft_nit, const int levels[NUM_LEV],
            const double dpde_l[NUM_LEV], const int tail_len[NUM_LEV],
            const double chg_leak_lt[MAX_TAIL_LEN*NUM_LEV],
            const double chg_open_lt[MAX_TAIL_LEN*NUM_LEV],
            const char *amp_name, const char *log_file);
int AddYCte(const int arrx, const int arry, const double sig_cte[arrx*arry],
            double sig_cor[arrx*arry], const double cte_frac,
            const int shft_nit, const int levels[NUM_LEV],
            const double dpde_l[NUM_LEV], const int tail_len[NUM_LEV],
            const double chg_leak_lt[MAX_TAIL_LEN*NUM_LEV],
            const double chg_open_lt[MAX_TAIL_LEN*NUM_LEV]);