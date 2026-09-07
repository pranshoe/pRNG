//===-- polyhedral_grand_suite.c - Grand Multi-Kernel Polyhedral Benchmark -===//
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
#include <string.h>

#define N_GEMM 256
#define N_2MM  128
#define N_3MM  128
#define N_MVT  256
#define N_BICG 256
#define N_SYRK 128
#define N_CONV 64
#define N_STENCIL 32
#define N_5D 16

#define ALIGN64 __attribute__((aligned(64)))

ALIGN64 static float A_2d[N_GEMM][N_GEMM];
ALIGN64 static float B_2d[N_GEMM][N_GEMM];
ALIGN64 static float C_2d[N_GEMM][N_GEMM];
ALIGN64 static float D_2d[N_GEMM][N_GEMM];
ALIGN64 static float E_2d[N_GEMM][N_GEMM];
ALIGN64 static float F_2d[N_GEMM][N_GEMM];
ALIGN64 static float G_2d[N_GEMM][N_GEMM];

ALIGN64 static float vec_x[N_MVT], vec_y[N_MVT], vec_u1[N_MVT], vec_u2[N_MVT], vec_v1[N_MVT], vec_v2[N_MVT];
ALIGN64 static float vec_p[N_BICG], vec_r[N_BICG], vec_s[N_BICG], vec_q[N_BICG];

ALIGN64 static float img_in[N_CONV][N_CONV];
ALIGN64 static float filter[5][5];
ALIGN64 static float img_out[N_CONV][N_CONV];

ALIGN64 static float grid3d_A[N_STENCIL][N_STENCIL][N_STENCIL];
ALIGN64 static float grid3d_B[N_STENCIL][N_STENCIL][N_STENCIL];

ALIGN64 static float tensor5d_A[N_5D][N_5D][N_5D][N_5D][N_5D];
ALIGN64 static float tensor5d_B[N_5D][N_5D][N_5D][N_5D][N_5D];

static double get_time_sec() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

// 1. GEMM
void __attribute__((noinline)) kernel_gemm(int n) {
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < n; k++) {
            for (int j = 0; j < n; j++) {
                C_2d[i][j] += A_2d[i][k] * B_2d[k][j];
            }
        }
    }
}

// 2. 2MM
void __attribute__((noinline)) kernel_2mm(int n) {
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < n; k++) {
            for (int j = 0; j < n; j++) {
                D_2d[i][j] += A_2d[i][k] * B_2d[k][j];
            }
        }
    }
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < n; k++) {
            for (int j = 0; j < n; j++) {
                E_2d[i][j] += D_2d[i][k] * C_2d[k][j];
            }
        }
    }
}

// 3. 3MM
void __attribute__((noinline)) kernel_3mm(int n) {
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < n; k++) {
            for (int j = 0; j < n; j++) {
                E_2d[i][j] += A_2d[i][k] * B_2d[k][j];
            }
        }
    }
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < n; k++) {
            for (int j = 0; j < n; j++) {
                F_2d[i][j] += C_2d[i][k] * D_2d[k][j];
            }
        }
    }
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < n; k++) {
            for (int j = 0; j < n; j++) {
                G_2d[i][j] += E_2d[i][k] * F_2d[k][j];
            }
        }
    }
}

// 4. MVT
void __attribute__((noinline)) kernel_mvt(int n) {
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            vec_x[i] += A_2d[i][j] * vec_y[j];
        }
    }
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            vec_u1[i] += A_2d[j][i] * vec_u2[j];
        }
    }
}

// 5. BiCG
void __attribute__((noinline)) kernel_bicg(int n) {
    for (int i = 0; i < n; i++) {
        vec_s[i] = 0.0f;
    }
    for (int i = 0; i < n; i++) {
        vec_q[i] = 0.0f;
        for (int j = 0; j < n; j++) {
            vec_s[j] += vec_r[i] * A_2d[i][j];
            vec_q[i] += A_2d[i][j] * vec_p[j];
        }
    }
}

// 6. SYRK
void __attribute__((noinline)) kernel_syrk(int n) {
    for (int i = 0; i < n; i++) {
        for (int j = 0; j <= i; j++) {
            for (int k = 0; k < n; k++) {
                C_2d[i][j] += A_2d[i][k] * A_2d[j][k];
            }
        }
    }
}

// 7. Conv2D
void __attribute__((noinline)) kernel_conv2d(int n) {
    for (int i = 2; i < n - 2; i++) {
        for (int j = 2; j < n - 2; j++) {
            float sum = 0.0f;
            for (int di = -2; di <= 2; di++) {
                for (int dj = -2; dj <= 2; dj++) {
                    sum += img_in[i + di][j + dj] * filter[di + 2][dj + 2];
                }
            }
            img_out[i][j] = sum;
        }
    }
}

// 8. Heat3D
void __attribute__((noinline)) kernel_heat3d(int n) {
    for (int i = 1; i < n - 1; i++) {
        for (int j = 1; j < n - 1; j++) {
            for (int k = 1; k < n - 1; k++) {
                grid3d_B[i][j][k] = 0.125f * (
                    grid3d_A[i+1][j][k] + grid3d_A[i-1][j][k] +
                    grid3d_A[i][j+1][k] + grid3d_A[i][j-1][k] +
                    grid3d_A[i][j][k+1] + grid3d_A[i][j][k-1] +
                    2.0f * grid3d_A[i][j][k]
                );
            }
        }
    }
}

// 9. Tensor5D
void __attribute__((noinline)) kernel_tensor5d(int dim) {
    for (int d0 = 0; d0 < dim; d0++) {
        for (int d1 = 0; d1 < dim; d1++) {
            for (int d2 = 0; d2 < dim; d2++) {
                for (int d3 = 0; d3 < dim; d3++) {
                    for (int d4 = 0; d4 < dim; d4++) {
                        tensor5d_B[d0][d1][d2][d3][d4] += 
                            tensor5d_A[d0][d1][d2][d3][d4] * 1.5f + 
                            (float)(d0 + d4) * 0.001f;
                    }
                }
            }
        }
    }
}

// 10. Loop Fusion
void __attribute__((noinline)) kernel_fusion_stream(int n) {
    for (int i = 0; i < n; i++) {
        vec_v1[i] = vec_u1[i] * 2.5f + 1.0f;
    }
    for (int i = 0; i < n; i++) {
        vec_v2[i] = vec_v1[i] * 3.14f + vec_u2[i];
    }
    for (int i = 0; i < n; i++) {
        vec_x[i] = vec_v2[i] * 0.5f - vec_y[i];
    }
}

// 11. Vector Reduction
float __attribute__((noinline)) kernel_vector_reduction(int n) {
    float s1 = 0.0f, s2 = 0.0f;
    for (int i = 0; i < n; i++) {
        s1 += vec_u1[i] * vec_u2[i];
        s2 += vec_v1[i] * vec_v2[i];
    }
    return s1 + s2;
}

// 12. Unaligned Contraction
void __attribute__((noinline)) kernel_unaligned_contraction(int n) {
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < n; k++) {
            for (int j = 0; j < n; j++) {
                C_2d[i][j] += (A_2d[i][k] + B_2d[k][j]) * 0.5f;
            }
        }
    }
}

int main(int argc, char **argv) {
    const char *label = (argc > 1) ? argv[1] : "ML-Clang -O3";

    for (int i = 0; i < N_GEMM; i++) {
        for (int j = 0; j < N_GEMM; j++) {
            A_2d[i][j] = (float)((i + j) % 17) * 0.1f;
            B_2d[i][j] = (float)((i * j) % 19) * 0.1f;
            C_2d[i][j] = 0.0f;
            D_2d[i][j] = 0.0f;
            E_2d[i][j] = 0.0f;
            F_2d[i][j] = 0.0f;
            G_2d[i][j] = 0.0f;
        }
    }
    for (int i = 0; i < N_MVT; i++) {
        vec_x[i] = 1.0f; vec_y[i] = 0.5f;
        vec_u1[i] = (float)i * 0.01f; vec_u2[i] = (float)i * 0.02f;
        vec_v1[i] = 0.0f; vec_v2[i] = 0.0f;
        vec_p[i] = 0.1f; vec_r[i] = 0.2f; vec_s[i] = 0.0f; vec_q[i] = 0.0f;
    }
    for (int i = 0; i < 5; i++) {
        for (int j = 0; j < 5; j++) filter[i][j] = 1.0f / 25.0f;
    }
    for (int i = 0; i < N_CONV; i++) {
        for (int j = 0; j < N_CONV; j++) {
            img_in[i][j] = (float)(i * j) * 0.05f;
            img_out[i][j] = 0.0f;
        }
    }

    const int ITERS_GEMM = 20;
    const int ITERS_2MM  = 50;
    const int ITERS_3MM  = 50;
    const int ITERS_MVT  = 500;
    const int ITERS_BICG = 500;
    const int ITERS_SYRK = 20;
    const int ITERS_CONV = 500;
    const int ITERS_HEAT = 20;
    const int ITERS_5D   = 10;
    const int ITERS_FUSE = 2000;
    const int ITERS_UNROLL = 10000;
    const int ITERS_UNALIGNED = 20;

    double t0 = get_time_sec();
    for (int it = 0; it < ITERS_GEMM; it++) kernel_gemm(N_GEMM);
    double t_gemm = get_time_sec() - t0;

    double t1 = get_time_sec();
    for (int it = 0; it < ITERS_2MM; it++) kernel_2mm(N_2MM);
    double t_2mm = get_time_sec() - t1;

    double t2 = get_time_sec();
    for (int it = 0; it < ITERS_3MM; it++) kernel_3mm(N_3MM);
    double t_3mm = get_time_sec() - t2;

    double t3 = get_time_sec();
    for (int it = 0; it < ITERS_MVT; it++) kernel_mvt(N_MVT);
    double t_mvt = get_time_sec() - t3;

    double t4 = get_time_sec();
    for (int it = 0; it < ITERS_BICG; it++) kernel_bicg(N_BICG);
    double t_bicg = get_time_sec() - t4;

    double t5 = get_time_sec();
    for (int it = 0; it < ITERS_SYRK; it++) kernel_syrk(N_SYRK);
    double t_syrk = get_time_sec() - t5;

    double t6 = get_time_sec();
    for (int it = 0; it < ITERS_CONV; it++) kernel_conv2d(N_CONV);
    double t_conv = get_time_sec() - t6;

    double t7 = get_time_sec();
    for (int it = 0; it < ITERS_HEAT; it++) kernel_heat3d(N_STENCIL);
    double t_heat = get_time_sec() - t7;

    double t8 = get_time_sec();
    for (int it = 0; it < ITERS_5D; it++) kernel_tensor5d(N_5D);
    double t_5d = get_time_sec() - t8;

    double t9 = get_time_sec();
    for (int it = 0; it < ITERS_FUSE; it++) kernel_fusion_stream(N_MVT);
    double t_fuse = get_time_sec() - t9;

    double t10 = get_time_sec();
    float volatile dummy = 0.0f;
    for (int it = 0; it < ITERS_UNROLL; it++) dummy += kernel_vector_reduction(N_MVT);
    double t_unroll = get_time_sec() - t10;

    double t11 = get_time_sec();
    for (int it = 0; it < ITERS_UNALIGNED; it++) kernel_unaligned_contraction(100);
    double t_unalign = get_time_sec() - t11;

    double t_total = t_gemm + t_2mm + t_3mm + t_mvt + t_bicg + t_syrk + t_conv + t_heat + t_5d + t_fuse + t_unroll + t_unalign;

    printf("==================================================================================\n");
    printf("   GRAND POLYHEDRAL & NUMERICAL COMPILATION BENCHMARK (%s)\n", label);
    printf("==================================================================================\n");
    printf("Kernel Category     | Kernel Name               | Problem Domain           | Time (sec)\n");
    printf("--------------------+---------------------------+--------------------------+-----------\n");
    printf("Dense Linear Alg    | GEMM                      | 2D (256 x 256)           | %7.4f s\n", t_gemm);
    printf("Matrix Pipeline     | 2MM (2 Matrix Multiply)   | 2D (128 x 128)           | %7.4f s\n", t_2mm);
    printf("Matrix Pipeline     | 3MM (3 Matrix Multiply)   | 2D (128 x 128)           | %7.4f s\n", t_3mm);
    printf("Vector / Transpose  | MVT                       | 1D/2D (256)              | %7.4f s\n", t_mvt);
    printf("Linear Solver       | BiCGStab Subkernel        | 1D/2D (256)              | %7.4f s\n", t_bicg);
    printf("Symmetric Alg       | SYRK (Triangular Update)  | 2D Triangular (128)      | %7.4f s\n", t_syrk);
    printf("Image Processing    | 2D Convolution (Filter)   | 2D (64 x 64, 5x5 filter) | %7.4f s\n", t_conv);
    printf("Stencil Computation | Heat-3D Diffusion         | 3D (32 x 32 x 32)        | %7.4f s\n", t_heat);
    printf("High-Dim Tensor     | Tensor-5D Contraction     | 5-D (16^5 = 1,048,576)   | %7.4f s\n", t_5d);
    printf("Producer-Consumer   | 3-Stage Fusion Pipeline   | 1D Streaming (256)       | %7.4f s\n", t_fuse);
    printf("Vector Reduction    | Multi-Acc Dot Product     | 1D (256)                 | %7.4f s\n", t_unroll);
    printf("Unaligned Loop      | Non-Power-of-2 Contraction| 3D (100 x 100 x 100)     | %7.4f s\n", t_unalign);
    printf("==================================================================================\n");
    printf(">>> TOTAL GRAND TOURNAMENT EXECUTION TIME: %7.4f s\n", t_total);
    printf("==================================================================================\n");

    return 0;
}
