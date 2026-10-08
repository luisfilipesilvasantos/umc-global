#include <iostream>
#include <new>
#include "cuda_runtime.h"
#include <map>

extern "C" {

typedef std::map<void*, bool> PtrMap;
PtrMap g_ptr_map;

__declspec(dllexport) void* my_alloc(size_t size, int device, cudaStream_t stream) {
    std::cerr << "Allocating: " << size << " bytes on device " << device << std::endl;
    void* ptr = nullptr;
    cudaSetDevice(device);
    cudaError_t err = cudaMalloc(&ptr, size);

    if (err == cudaErrorMemoryAllocation) {
        std::cerr << "Falling back to RAM (pinned)" << std::endl;
        err = cudaMallocHost(&ptr, size);
        if (err != cudaSuccess) {
            std::cerr << "cudaMallocHost falhou: " << cudaGetErrorString(err) << std::endl;
            throw std::bad_alloc();
        }
        g_ptr_map[ptr] = true;
        std::cerr << "Tier: RAM (pinned, fallback)" << std::endl;
    } else if (err != cudaSuccess) {
        std::cerr << "cudaMalloc falhou: " << cudaGetErrorString(err) << std::endl;
        throw std::bad_alloc();
    } else {
        g_ptr_map[ptr] = false;
        std::cerr << "Tier: VRAM" << std::endl;
    }

    return ptr;
}

__declspec(dllexport) void my_free(void* ptr, size_t size, int device, cudaStream_t stream) {
    std::cerr << "Freeing: " << size << " bytes on device " << device << std::endl;
    cudaSetDevice(device);

    if (g_ptr_map.find(ptr) == g_ptr_map.end()) {
        std::cerr << "Pointer not found in map" << std::endl;
        return;
    }

    bool is_pinned = g_ptr_map[ptr];
    g_ptr_map.erase(ptr);

    if (is_pinned) {
        cudaFreeHost(ptr);
        std::cerr << "Tier: RAM (pinned, fallback)" << std::endl;
    } else {
        cudaFree(ptr);
        std::cerr << "Tier: VRAM" << std::endl;
    }
}

}