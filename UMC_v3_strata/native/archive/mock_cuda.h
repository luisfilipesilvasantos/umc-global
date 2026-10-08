#pragma once

// Minimal MOCK implementation for CUDA types/functions required only for compilation simulation.
// In a real environment, these definitions would come from the actual SDK headers.
#ifndef CUDA_RUNTIME_H
#define CUDA_RUNTIME_H

// Mock Stream Handle
typedef unsigned long cudaStream_t;
const cudaStream_t nullptr = 0; // Using 0 as null stream handle placeholder

// Mock Error/Device functions (These return success or default values for compilation purposes)
namespace cuMock {
    typedef int cudaError_t;
    #define cudaSuccess 0
    const cudaError_t cudaFailure = 1;
}

// Mock CUDA API wrappers to allow compilation without linking external libraries.
cudaError_t cudaGetDeviceCount(int* deviceCount) {
    if (deviceCount) *deviceCount = 1; // Simulate finding at least one GPU if needed
    return cuMock::cudaSuccess;
}

cudaError_t cudaGetDeviceProperties(struct cudaDeviceProp* prop, int device);
// Define a mock structure that contains necessary fields for the class.
struct cudaDeviceProp {
    int deviceId;
    char name[256];
    size_t totalGlobalMem; // Mock field used in GpuDeviceInfo
};

cudaError_t cudaStreamCreate(cudaStream_t* stream) {
    if (stream) *stream = 1; 
    return cuMock::cudaSuccess;
}

void cudaSetDevice(int deviceId) { /* No op */ }

void cudaStreamDestroy(cudaStream_t stream) { /* No op */ }

void cudaStreamSynchronize(cudaStream_t stream) {
    // In a real setup, this blocks until the stream finishes.
}

// We avoid defining memory allocation functions (e.g., cudaMalloc) that would require linking external libraries,
// as the core logic relies on simulating internal contiguous block access anyway.

#endif // CUDA_RUNTIME_H