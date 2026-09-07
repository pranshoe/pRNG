//===-- test_ml_suite.c - Complete ML Optimizer Test Suite ------*- C -*-===//
//
// This test program contains 3 distinct loop kernels designed to exercise
// all three in-tree ML passes:
//   1. MLFusionPass   -> Producer-Consumer Pipeline (data reuse across loops)
//   2. MLTilingPass   -> Large Cache-Bound Matrix Multiply (N = 1024)
//   3. MLUnrollPass   -> Vector Dot Product / Reduction (Pipelining & SIMD)
//
//===----------------------------------------------------------------------===//

#include <stdio.h>
#include <stdlib.h>

#define N 1024

// Global test buffers
float A[N][N], B_mat[N][N], C_mat[N][N];
float vecA[N], vecB[N], vecC[N], vecD[N];

// ============================================================================
// 1. KERNEL 1: Exercises MLFusionPass
// Two adjacent loops that share buffer vecB (Producer -> Consumer).
// MLFusionPass should detect SharedBuffers=1 and fuse them to keep vecB in YMM registers!
// ============================================================================
void pipeline_producer_consumer(int size) {
    // Loop 1: Producer
    for (int i = 0; i < size; i++) {
        vecB[i] = vecA[i] * 2.5f + 1.0f;
    }

    // Loop 2: Consumer (Reads vecB immediately)
    for (int i = 0; i < size; i++) {
        vecC[i] = vecB[i] * 3.0f + vecD[i];
    }
}

// ============================================================================
// 2. KERNEL 2: Exercises MLTilingPass
// Large 2D matrix multiplication (12.6 MB footprint).
// MLTilingPass should detect cache thrashing and apply multi-dimensional tiling!
// ============================================================================
void matrix_multiply(int size) {
    for (int i = 0; i < size; i++) {
        for (int k = 0; k < size; k++) {
            for (int j = 0; j < size; j++) {
                C_mat[i][j] += A[i][k] * B_mat[k][j];
            }
        }
    }
}

// ============================================================================
// 3. KERNEL 3: Exercises MLUnrollPass
// Innermost vector reduction.
// MLUnrollPass should evaluate register pressure and select unroll factor (e.g. 8).
// ============================================================================
float vector_dot_product(int size) {
    float sum = 0.0f;
    for (int i = 0; i < size; i++) {
        sum += vecA[i] * vecB[i];
    }
    return sum;
}

int main() {
    printf("Initializing ML Test Suite (N = %d)...\n", N);
    for (int i = 0; i < N; i++) {
        vecA[i] = (float)i * 0.1f;
        vecD[i] = (float)i * 0.2f;
        for (int j = 0; j < N; j++) {
            A[i][j] = (float)(i + j) * 0.01f;
            B_mat[i][j] = (float)(i - j) * 0.01f;
            C_mat[i][j] = 0.0f;
        }
    }

    // Run tests
    pipeline_producer_consumer(N);
    matrix_multiply(N);
    float sum = vector_dot_product(N);

    printf("Done! Verification Sum: %.4f | C_mat[10][10]: %.4f\n", sum, C_mat[10][10]);
    return 0;
}
