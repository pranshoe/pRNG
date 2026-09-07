//===-- ai_dsp_hpc_benchmarks.c - AI, DSP & Advanced HPC Benchmark Suite -===//
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <time.h>
#include <string.h>

#define ALIGN64 __attribute__((aligned(64)))

#define ATTN_SEQ 512
#define ATTN_DIM 64
#define LLM_K 2048
#define LLM_N 2048
#define FFT_N 4096
#define MG_N 32
#define NBODY_N 1024
#define CONV3D_D 16
#define CONV3D_H 32
#define CONV3D_W 32

static double get_time_sec() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

// 1. FlashAttention-2 Tiled Kernel
ALIGN64 static float Q[ATTN_SEQ][ATTN_DIM];
ALIGN64 static float K[ATTN_SEQ][ATTN_DIM];
ALIGN64 static float V[ATTN_SEQ][ATTN_DIM];
ALIGN64 static float O[ATTN_SEQ][ATTN_DIM];
ALIGN64 static float S_block[64][64];

void __attribute__((noinline)) kernel_flash_attention(int seq_len, int d) {
    const int Br = 64;
    const int Bc = 64;
    const float scale = 1.0f / sqrtf((float)d);

    for (int i = 0; i < seq_len; i += Br) {
        int i_max = (i + Br < seq_len) ? i + Br : seq_len;
        for (int j = 0; j < seq_len; j += Bc) {
            int j_max = (j + Bc < seq_len) ? j + Bc : seq_len;

            for (int r = i; r < i_max; r++) {
                for (int c = j; c < j_max; c++) {
                    float dot = 0.0f;
                    for (int k = 0; k < d; k++) {
                        dot += Q[r][k] * K[c][k];
                    }
                    S_block[r - i][c - j] = expf(dot * scale * 0.1f);
                }
            }

            for (int r = i; r < i_max; r++) {
                for (int k = 0; k < d; k++) {
                    float sum = 0.0f;
                    for (int c = j; c < j_max; c++) {
                        sum += S_block[r - i][c - j] * V[c][k];
                    }
                    O[r][k] += sum;
                }
            }
        }
    }
}

// 2. LLM Autoregressive Decode GEMV (M=1 Generative Phase)
ALIGN64 static float llm_weight[LLM_N][LLM_K];
ALIGN64 static float llm_in[LLM_K];
ALIGN64 static float llm_bias[LLM_N];
ALIGN64 static float llm_out[LLM_N];

void __attribute__((noinline)) kernel_llm_gemv(int n, int k) {
    for (int i = 0; i < n; i++) {
        float sum = llm_bias[i];
        for (int j = 0; j < k; j++) {
            sum += llm_weight[i][j] * llm_in[j];
        }
        llm_out[i] = sum;
    }
}

// 3. Fused Residual Addition + RMSNorm
ALIGN64 static float norm_x[LLM_K];
ALIGN64 static float norm_residual[LLM_K];
ALIGN64 static float norm_gamma[LLM_K];
ALIGN64 static float norm_out[LLM_K];

void __attribute__((noinline)) kernel_fused_rmsnorm(int n) {
    float sum_sq = 0.0f;
    for (int i = 0; i < n; i++) {
        norm_x[i] += norm_residual[i];
        sum_sq += norm_x[i] * norm_x[i];
    }

    float rms = 1.0f / sqrtf(sum_sq / (float)n + 1e-6f);
    for (int i = 0; i < n; i++) {
        norm_out[i] = norm_x[i] * rms * norm_gamma[i];
    }
}

// 4. 3D Spatial UNet Convolution
ALIGN64 static float conv3d_in[CONV3D_D][CONV3D_H][CONV3D_W];
ALIGN64 static float conv3d_filter[3][3][3];
ALIGN64 static float conv3d_out[CONV3D_D][CONV3D_H][CONV3D_W];

void __attribute__((noinline)) kernel_conv3d(int D, int H, int W) {
    for (int d = 1; d < D - 1; d++) {
        for (int h = 1; h < H - 1; h++) {
            for (int w = 1; w < W - 1; w++) {
                float sum = 0.0f;
                for (int kd = -1; kd <= 1; kd++) {
                    for (int kh = -1; kh <= 1; kh++) {
                        for (int kw = -1; kw <= 1; kw++) {
                            sum += conv3d_in[d + kd][h + kh][w + kw] * conv3d_filter[kd + 1][kh + 1][kw + 1];
                        }
                    }
                }
                conv3d_out[d][h][w] = sum;
            }
        }
    }
}

// 5. 3D Multigrid Gauss-Seidel Stencil
ALIGN64 static float mg_u[MG_N][MG_N][MG_N];
ALIGN64 static float mg_rhs[MG_N][MG_N][MG_N];

void __attribute__((noinline)) kernel_multigrid_3d(int n) {
    const float h2inv = 1.0f / 6.0f;
    for (int i = 1; i < n - 1; i++) {
        for (int j = 1; j < n - 1; j++) {
            for (int k = 1; k < n - 1; k++) {
                mg_u[i][j][k] = (mg_rhs[i][j][k] +
                    mg_u[i-1][j][k] + mg_u[i+1][j][k] +
                    mg_u[i][j-1][k] + mg_u[i][j+1][k] +
                    mg_u[i][j][k-1] + mg_u[i][j][k+1]) * h2inv;
            }
        }
    }
}

// 6. 2D 8x8 DCT
ALIGN64 static float dct_block[8][8];
ALIGN64 static float dct_coeff[8][8];
ALIGN64 static float dct_out[8][8];

void __attribute__((noinline)) kernel_dct_8x8() {
    for (int u = 0; u < 8; u++) {
        for (int v = 0; v < 8; v++) {
            float sum = 0.0f;
            for (int x = 0; x < 8; x++) {
                for (int y = 0; y < 8; y++) {
                    sum += dct_block[x][y] * dct_coeff[u][x] * dct_coeff[v][y];
                }
            }
            dct_out[u][v] = sum;
        }
    }
}

// 7. N-Body Direct Simulation
ALIGN64 static float body_x[NBODY_N], body_y[NBODY_N], body_z[NBODY_N], body_m[NBODY_N];
ALIGN64 static float body_fx[NBODY_N], body_fy[NBODY_N], body_fz[NBODY_N];

void __attribute__((noinline)) kernel_nbody(int n) {
    const float epssq = 0.01f;
    for (int i = 0; i < n; i++) {
        float pfx = 0.0f, pfy = 0.0f, pfz = 0.0f;
        float px = body_x[i], py = body_y[i], pz = body_z[i];
        for (int j = 0; j < n; j++) {
            float dx = body_x[j] - px;
            float dy = body_y[j] - py;
            float dz = body_z[j] - pz;
            float distsq = dx * dx + dy * dy + dz * dz + epssq;
            float invdist = 1.0f / sqrtf(distsq);
            float invdist3 = invdist * invdist * invdist;
            float f = body_m[j] * invdist3;
            pfx += dx * f;
            pfy += dy * f;
            pfz += dz * f;
        }
        body_fx[i] = pfx;
        body_fy[i] = pfy;
        body_fz[i] = pfz;
    }
}

// 8. Radix-2 1D FFT
ALIGN64 static float fft_real[FFT_N];
ALIGN64 static float fft_imag[FFT_N];

void __attribute__((noinline)) kernel_fft_radix2(int n) {
    for (int len = 2; len <= n; len <<= 1) {
        float angle = -2.0f * 3.14159265f / (float)len;
        float wlen_r = cosf(angle);
        float wlen_i = sinf(angle);
        for (int i = 0; i < n; i += len) {
            float w_r = 1.0f;
            float w_i = 0.0f;
            for (int j = 0; j < len / 2; j++) {
                int u = i + j;
                int v = i + j + len / 2;
                float tr = fft_real[v] * w_r - fft_imag[v] * w_i;
                float ti = fft_real[v] * w_i + fft_imag[v] * w_r;
                fft_real[v] = fft_real[u] - tr;
                fft_imag[v] = fft_imag[u] - ti;
                fft_real[u] = fft_real[u] + tr;
                fft_imag[u] = fft_imag[u] + ti;
                float nw_r = w_r * wlen_r - w_i * wlen_i;
                w_i = w_r * wlen_i + w_i * wlen_r;
                w_r = nw_r;
            }
        }
    }
}

int main(int argc, char **argv) {
    const char *label = (argc > 1) ? argv[1] : "ML-Clang -O3";

    for (int i = 0; i < ATTN_SEQ; i++) {
        for (int j = 0; j < ATTN_DIM; j++) {
            Q[i][j] = (float)((i + j) % 13) * 0.01f;
            K[i][j] = (float)((i * j) % 17) * 0.01f;
            V[i][j] = (float)((i - j) % 19) * 0.01f;
            O[i][j] = 0.0f;
        }
    }
    for (int i = 0; i < LLM_N; i++) {
        llm_bias[i] = (float)i * 0.001f;
        for (int j = 0; j < LLM_K; j++) {
            llm_weight[i][j] = (float)((i + j) % 23) * 0.001f;
        }
    }
    for (int i = 0; i < LLM_K; i++) {
        llm_in[i] = 1.0f;
        norm_x[i] = (float)i * 0.01f;
        norm_residual[i] = (float)i * 0.005f;
        norm_gamma[i] = 1.0f;
    }
    for (int i = 0; i < NBODY_N; i++) {
        body_x[i] = (float)i * 0.1f;
        body_y[i] = (float)i * 0.2f;
        body_z[i] = (float)i * 0.3f;
        body_m[i] = 1.0f;
    }
    for (int i = 0; i < FFT_N; i++) {
        fft_real[i] = sinf((float)i * 0.05f);
        fft_imag[i] = 0.0f;
    }

    const int ITERS_ATTN = 20;
    const int ITERS_GEMV = 200;
    const int ITERS_NORM = 30000;
    const int ITERS_CONV3D = 300;
    const int ITERS_MG = 300;
    const int ITERS_DCT = 300000;
    const int ITERS_NBODY = 50;
    const int ITERS_FFT = 300;

    double t0 = get_time_sec();
    for (int it = 0; it < ITERS_ATTN; it++) kernel_flash_attention(ATTN_SEQ, ATTN_DIM);
    double t_attn = get_time_sec() - t0;

    double t1 = get_time_sec();
    for (int it = 0; it < ITERS_GEMV; it++) kernel_llm_gemv(LLM_N, LLM_K);
    double t_gemv = get_time_sec() - t1;

    double t2 = get_time_sec();
    for (int it = 0; it < ITERS_NORM; it++) kernel_fused_rmsnorm(LLM_K);
    double t_norm = get_time_sec() - t2;

    double t3 = get_time_sec();
    for (int it = 0; it < ITERS_CONV3D; it++) kernel_conv3d(CONV3D_D, CONV3D_H, CONV3D_W);
    double t_conv3d = get_time_sec() - t3;

    double t4 = get_time_sec();
    for (int it = 0; it < ITERS_MG; it++) kernel_multigrid_3d(MG_N);
    double t_mg = get_time_sec() - t4;

    double t5 = get_time_sec();
    for (int it = 0; it < ITERS_DCT; it++) kernel_dct_8x8();
    double t_dct = get_time_sec() - t5;

    double t6 = get_time_sec();
    for (int it = 0; it < ITERS_NBODY; it++) kernel_nbody(NBODY_N);
    double t_nbody = get_time_sec() - t6;

    double t7 = get_time_sec();
    for (int it = 0; it < ITERS_FFT; it++) kernel_fft_radix2(FFT_N);
    double t_fft = get_time_sec() - t7;

    double t_total = t_attn + t_gemv + t_norm + t_conv3d + t_mg + t_dct + t_nbody + t_fft;

    printf("========================================================================================\n");
    printf("     AI, DSP & ADVANCED HPC BENCHMARK TOURNAMENT (%s)\n", label);
    printf("========================================================================================\n");
    printf("Workload Category  | Kernel Name               | Problem Domain            | Time (sec)\n");
    printf("-------------------+---------------------------+---------------------------+-----------\n");
    printf("Modern AI / LLM    | FlashAttention-2 (Tiled)  | Seq=512, D=64             | %7.4f s\n", t_attn);
    printf("Modern AI / LLM    | LLM Decode GEMV (M=1)     | 1 x 2048 x 2048           | %7.4f s\n", t_gemv);
    printf("Modern AI / LLM    | Fused RMSNorm + Residual  | Hidden Dim=2048           | %7.4f s\n", t_norm);
    printf("Computer Vision    | 3D UNet Convolution       | 16 x 32 x 32 (3x3x3)      | %7.4f s\n", t_conv3d);
    printf("HPC Multigrid      | 3D Gauss-Seidel Stencil   | 32 x 32 x 32              | %7.4f s\n", t_mg);
    printf("DSP / Video Codec  | 2D 8x8 DCT (JPEG/HEVC)    | 8 x 8 Transform Matrix    | %7.4f s\n", t_dct);
    printf("N-Body Physics     | Gravitational Direct N^2  | N = 1024 Particles        | %7.4f s\n", t_nbody);
    printf("Signal Processing  | Radix-2 Complex 1D FFT    | N = 4096 Samples          | %7.4f s\n", t_fft);
    printf("========================================================================================\n");
    printf(">>> TOTAL AI/DSP/HPC SUITE EXECUTION TIME: %7.4f s\n", t_total);
    printf("========================================================================================\n");

    return 0;
}
