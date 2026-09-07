//===-- benchmark_suite.c - Full Benchmark: LLVM Stock vs ML Optimizer -===//
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define N 512
#define ITERS_PIPE   50000
#define ITERS_MAT    50
#define ITERS_DOT    500000

// Buffers
float A[N][N], B_mat[N][N], C_mat[N][N];
float vecA[N], vecB[N], vecC[N], vecD[N];

// 1. Producer-Consumer Pipeline (Target for Loop Fusion)
void __attribute__((noinline)) test_pipeline(int size) {
    for (int i = 0; i < size; i++) {
        vecB[i] = vecA[i] * 2.5f + 1.0f;
    }
    for (int i = 0; i < size; i++) {
        vecC[i] = vecB[i] * 3.0f + vecD[i];
    }
}

// 2. Matrix Multiplication (Target for Loop Tiling)
void __attribute__((noinline)) test_matrix_mult(int size) {
    for (int i = 0; i < size; i++) {
        for (int k = 0; k < size; k++) {
            for (int j = 0; j < size; j++) {
                C_mat[i][j] += A[i][k] * B_mat[k][j];
            }
        }
    }
}

// 3. Vector Dot Product (Target for Loop Unrolling)
float __attribute__((noinline)) test_dot_product(int size) {
    float sum = 0.0f;
    for (int i = 0; i < size; i++) {
        sum += vecA[i] * vecB[i];
    }
    return sum;
}

double get_time_sec() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    const char *label = (argc > 1) ? argv[1] : "Standard";

    // Initialize data
    for (int i = 0; i < N; i++) {
        vecA[i] = (float)i * 0.01f;
        vecD[i] = (float)i * 0.02f;
        for (int j = 0; j < N; j++) {
            A[i][j] = (float)(i + j) * 0.001f;
            B_mat[i][j] = (float)(i - j) * 0.001f;
            C_mat[i][j] = 0.0f;
        }
    }

    // Warmup
    test_pipeline(N);
    test_matrix_mult(N);
    test_dot_product(N);

    // 1. Measure Pipeline
    double t0 = get_time_sec();
    for (int it = 0; it < ITERS_PIPE; it++) test_pipeline(N);
    double t_pipe = get_time_sec() - t0;

    // 2. Measure Matrix Mult
    double t1 = get_time_sec();
    for (int it = 0; it < ITERS_MAT; it++) test_matrix_mult(N);
    double t_mat = get_time_sec() - t1;

    // 3. Measure Dot Product
    double t2 = get_time_sec();
    float volatile dummy = 0.0f;
    for (int it = 0; it < ITERS_DOT; it++) dummy += test_dot_product(N);
    double t_dot = get_time_sec() - t2;

    double t_total = t_pipe + t_mat + t_dot;

    printf("[%s Results]\n", label);
    printf("  1. Producer-Consumer Pipeline (%d iters) : %7.4f s\n", ITERS_PIPE, t_pipe);
    printf("  2. Matrix Multiplication     (%d iters) : %7.4f s\n", ITERS_MAT, t_mat);
    printf("  3. Vector Dot Product        (%d iters) : %7.4f s\n", ITERS_DOT, t_dot);
    printf("  -> Total Execution Time                    : %7.4f s\n", t_total);

    return 0;
}
