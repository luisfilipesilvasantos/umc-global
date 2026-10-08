#pragma once
#include <windows.H>
#include <string>

#ifndef CUDA_RUNTIME_MOCK_H_
#define CUDA_RUNTIME_MOCK_H_

#ifdef USE_CUDA_MOCK

typedef void* cudaStream_t;

typedef enum cudaError_e {
    cudaSuccess = 0,
    cudaErrorMemoryAllocation = 2,
    cudaErrorNotInitialized = 3,
    cudaErrorInvalidDevice = 101
} cudaError_t;


typedef void* cudaHostPtr_t;

enum cudaMemcpyKind {
    cudaMemcpyHostToHost = 0,
    cudaMemcpyHostToDevice = 1,
    cudaMemcpyDeviceToHost = 2,
    cudaMemcpyDeviceToDevice = 3
};

#else

#include <cuda_runtime_api.h>

#endif

// Funções CUDA stub para compilação sem CUDA SDK real
inline cudaError_t cudaSetDevice(int device) { return cudaSuccess; }
inline cudaError_t cudaMalloc(void** devPtr, size_t size) {
    *devPtr = nullptr;
    return cudaErrorMemoryAllocation;
}
inline cudaError_t cudaMemset(void* devPtr, int value, size_t count) { return cudaSuccess; }
inline cudaError_t cudaFree(void* devPtr) { return cudaSuccess; }
inline cudaError_t cudaGetLastError() { return cudaSuccess; }
inline const char* cudaGetErrorString(cudaError_t error) { return "mocked"; }

// Funções de stream stub
inline cudaError_t cudaStreamCreateWithFlags(cudaStream_t* pStream, unsigned int flags) {
    *pStream = nullptr;
    return cudaSuccess;
}
inline cudaError_t cudaStreamCreate(cudaStream_t* pStream) {
    *pStream = nullptr;
    return cudaSuccess;
}
inline cudaError_t cudaStreamDestroy(cudaStream_t stream) { return cudaSuccess; }
inline cudaError_t cudaStreamSynchronize(cudaStream_t stream) { return cudaSuccess; }

// Funções de host pinning stub
inline cudaError_t cudaHostRegister(void* devPtr, size_t size, unsigned int flags) { return cudaSuccess; }
inline cudaError_t cudaHostUnregister(void* devPtr) { return cudaSuccess; }

// Função cudaMemcpy stub - apenas para compilação, não faz nada útil
inline cudaError_t cudaMemcpy(void* dst, const void* src, size_t count, enum cudaMemcpyKind kind) {
    return cudaSuccess;
}

#endif