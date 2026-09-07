//===-- benchmark_5d_tiling.c - Multi-Dimensional LLVM ML Benchmark Suite -===//
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <math.h>

#define N2D 512
#define N3D 100
#define N5D 16

#define ITERS_FUSION  20000
#define ITERS_MAT2D   50
#define ITERS_MAT3D   100
#define ITERS_5D      20
#define ITERS_UNROLL  200000

// Memory Buffers
float A_2d[N2D][N2D], B_2d[N2D][N2D], C_2d[N2D][N2D];
float A_3d[N3D][N3D], B_3d[N3D][N3D], C_3d[N3D][N3D];
float tensor_5d[N5D][N5D][N5D][N5D][N5D];
float vec_a[N2D], vec_b[N2D], vec_c[N2D], vec_d[N2D];

// 1. Producer-Consumer Fused Pipeline (Target for MLFusionPass)
void __attribute__((noinline)) test_fusion_pipeline(int size) {
    for (int i = 0; i < size; i++) {
        vec_b[i] = vec_a[i] * 2.5f + 1.0f;
    }
    for (int i = 0; i < size; i++) {
        vec_c[i] = vec_b[i] * 3.0f + vec_d[i];
    }
}

// 2. 2D Matrix Multiplication (Target for MLTilingPass 2D)
void __attribute__((noinline)) test_matmul_2d(int size) {
    for (int i = 0; i < size; i++) {
        for (int k = 0; k < size; k++) {
            for (int j = 0; j < size; j++) {
                C_2d[i][j] += A_2d[i][k] * B_2d[k][j];
            }
        }
    }
}

// 3. 3D Non-Power-of-Two Matrix Contraction (Target for Divisor-Matched Remainder Tiling)
void __attribute__((noinline)) test_matmul_3d_unaligned(int size) {
    for (int i = 0; i < size; i++) {
        for (int k = 0; k < size; k++) {
            for (int j = 0; j < size; j++) {
                C_3d[i][j] += A_3d[i][k] * B_3d[k][j];
            }
        }
    }
}

// 4. 5-D Deep Nested Loop Tensor Contraction (Target for 5-D Structural Model)
void __attribute__((noinline)) test_tensor_5d(int dim) {
    for (int d0 = 0; d0 < dim; d0++) {
        for (int d1 = 0; d1 < dim; d1++) {
            for (int d2 = 0; d2 < dim; d2++) {
                for (int d3 = 0; d3 < dim; d3++) {
                    for (int d4 = 0; d4 < dim; d4++) {
                        tensor_5d[d0][d1][d2][d3][d4] += 
                            (float)(d0 + d1) * 0.01f + 
                            (float)(d2 * d3) * 0.001f + 
                            (float)d4 * 0.0001f;
                    }
                }
            }
        }
    }
}

// 5. Vector Dot Product (Target for MLUnrollPass)
float __attribute__((noinline)) test_dot_product(int size) {
    float sum = 0.0f;
    for (int i = 0; i < size; i++) {
        sum += vec_a[i] * vec_b[i];
    }
    return sum;
}

double get_time_sec() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main(int argc, char **argv) {
    const char *label = (argc > 1) ? argv[1] : "ML Optimizer";

    // Initialize arrays
    for (int i = 0; i < N2D; i++) {
        vec_a[i] = (float)i * 0.01f;
        vec_d[i] = (float)i * 0.02f;
        for (int j = 0; j < N2D; j++) {
            A_2d[i][j] = (float)(i + j) * 0.001f;
            B_2d[i][j] = (float)(i - j) * 0.001f;
            C_2d[i][j] = 0.0f;
        }
    }
    for (int i = 0; i < N3D; i++) {
        for (int j = 0; j < N3D; j++) {
            A_3d[i][j] = (float)(i + j) * 0.002f;
            B_3d[i][j] = (float)(i - j) * 0.002f;
            C_3d[i][j] = 0.0f;
        }
    }

    // Warmup
    test_fusion_pipeline(N2D);
    test_matmul_2d(N2D);
    test_matmul_3d_unaligned(N3D);
    test_tensor_5d(N5D);
    test_dot_product(N2D);

    printf("==========================================================================\n");
    printf("     BENCHMARKING NATIVE IN-TREE ML PIPELINE (%s)\n", label);
    printf("==========================================================================\n");

    // 1. Fusion
    double t0 = get_time_sec();
    for (int it = 0; it < ITERS_FUSION; it++) test_fusion_pipeline(N2D);
    double t_fuse = get_time_sec() - t0;

    // 2. 2D Matmul
    double t1 = get_time_sec();
    for (int it = 0; it < ITERS_MAT2D; it++) test_matmul_2d(N2D);
    double t_mat2d = get_time_sec() - t1;

    // 3. 3D Matmul (Unaligned)
    double t2 = get_time_sec();
    for (int it = 0; it < ITERS_MAT3D; it++) test_matmul_3d_unaligned(N3D);
    double t_mat3d = get_time_sec() - t2;

    // 4. 5D Tensor Contraction
    double t3 = get_time_sec();
    for (int it = 0; it < ITERS_5D; it++) test_tensor_5d(N5D);
    double t_5d = get_time_sec() - t3;

    // 5. Unrolled Dot Product
    double t4 = get_time_sec();
    float volatile dummy = 0.0f;
    for (int it = 0; it < ITERS_UNROLL; it++) dummy += test_dot_product(N2D);
    double t_unroll = get_time_sec() - t4;

    double t_total = t_fuse + t_mat2d + t_mat3d + t_5d + t_unroll;

    printf("\nExecution Breakdown:\n");
    printf("  1. Fused Pipeline (1D, %d iters)        : %7.4f s\n", ITERS_FUSION, t_fuse);
    printf("  2. Matrix Multiply (2D, %d iters)       : %7.4f s\n", ITERS_MAT2D, t_mat2d);
    printf("  3. Non-aligned Contraction (3D, %d iters): %7.4f s\n", ITERS_MAT3D, t_mat3d);
    printf("  4. Tensor Contraction (5-D, %d iters)    : %7.4f s\n", ITERS_5D, t_5d);
    printf("  5. Vector Dot Product (Unroll, %d iters) : %7.4f s\n", ITERS_UNROLL, t_unroll);
    printf("  --------------------------------------------------------\n");
    printf("  -> Total Execution Time                  : %7.4f s\n", t_total);
    printf("==========================================================================\n");

    return 0;
}
