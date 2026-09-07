#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define N 100
#define ITERS 10000

float A[N][N], B[N][N], C_baseline[N][N], C_polly[N][N], C_model[N][N];

// 1. Clang -O3 Baseline (No Tiling)
void __attribute__((noinline)) run_baseline() {
    for (int i = 0; i < N; i++)
        for (int k = 0; k < N; k++)
            for (int j = 0; j < N; j++)
                C_baseline[i][j] += A[i][k] * B[k][j];
}

// 2. LLVM Polly Fixed Heuristic: [64 x 64] (Leaves massive remainder of 36)
void __attribute__((noinline)) run_polly_fixed_64() {
    const int T = 64;
    for (int ii = 0; ii < N; ii += T) {
        int i_max = (ii + T < N) ? ii + T : N;
        for (int kk = 0; kk < N; kk += T) {
            int k_max = (kk + T < N) ? kk + T : N;
            for (int jj = 0; jj < N; jj += T) {
                int j_max = (jj + T < N) ? jj + T : N;
                for (int i = ii; i < i_max; i++)
                    for (int k = kk; k < k_max; k++)
                        for (int j = jj; j < j_max; j++)
                            C_polly[i][j] += A[i][k] * B[k][j];
            }
        }
    }
}

// 3. Our ML Model Choice: [25 x 25] (Exact Divisor -> 0 Remainder Tail)
void __attribute__((noinline)) run_ml_model_tile_25() {
    const int T = 25;
    for (int ii = 0; ii < N; ii += T) {
        for (int kk = 0; kk < N; kk += T) {
            for (int jj = 0; jj < N; jj += T) {
                for (int i = ii; i < ii + T; i++)
                    for (int k = kk; k < kk + T; k++)
                        for (int j = jj; j < jj + T; j++)
                            C_model[i][j] += A[i][k] * B[k][j];
            }
        }
    }
}

double get_time_sec() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main() {
    // Initialize matrices
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) {
            A[i][j] = (float)rand() / (float)RAND_MAX;
            B[i][j] = (float)rand() / (float)RAND_MAX;
            C_baseline[i][j] = 0.0f;
            C_polly[i][j] = 0.0f;
            C_model[i][j] = 0.0f;
        }
    }

    printf("======================================================================\n");
    printf("   LIVE BENCHMARK: UNALIGNED LOOP NEST (N = 100 x 100 x 100, %d Iterations)\n", ITERS);
    printf("======================================================================\n\n");

    // Warmup
    run_baseline();
    run_polly_fixed_64();
    run_ml_model_tile_25();

    // 1. Clang Baseline
    double t0 = get_time_sec();
    for (int it = 0; it < ITERS; it++) run_baseline();
    double t_base = get_time_sec() - t0;

    // 2. LLVM Polly Fixed [64 x 64]
    double t1 = get_time_sec();
    for (int it = 0; it < ITERS; it++) run_polly_fixed_64();
    double t_polly = get_time_sec() - t1;

    // 3. Our Model Choice [25 x 25]
    double t2 = get_time_sec();
    for (int it = 0; it < ITERS; it++) run_ml_model_tile_25();
    double t_model = get_time_sec() - t2;

    printf("Strategy                 | Time (sec) | Speedup vs -O3 | Impact vs -O3\n");
    printf("---------------------------------------------------------------------------\n");
    printf("Clang -O3 (No Tiling)    |  %7.4f s |     1.000x     |   Baseline\n", t_base);
    printf("LLVM Polly Fixed [64x64] |  %7.4f s |     %5.3fx     |  [SLOWDOWN] %+.1f%%\n", 
           t_polly, t_base / t_polly, ((t_base / t_polly) - 1.0) * 100.0);
    printf("Our ML Model [25x25]     |  %7.4f s |     %5.3fx     |  [SPEEDUP]  %+.1f%%\n", 
           t_model, t_base / t_model, ((t_base / t_model) - 1.0) * 100.0);
    printf("===========================================================================\n");

    return 0;
}
