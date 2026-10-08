#include "VirtualGpuMemory.h"
#include <cuda_runtime.h>
#include <unordered_map>
#include <mutex>

static std::unordered_map<void*, size_t> g_allocations;
static std::mutex g_mutex;
static bool g_initialized = false;
static size_t g_totalAllocated = 0;
static size_t g_peakAllocated = 0;
static int g_deviceCount = 0;
static size_t g_vramTotal = 0;

extern "C" {

void umc_init_allocator(size_t maxVramBudget, size_t maxRamBudget) {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_initialized) return;

    cudaError_t err = cudaGetDeviceCount(&g_deviceCount);
    if (err != cudaSuccess || g_deviceCount == 0) {
        return;
    }

    for (int i = 0; i < g_deviceCount; ++i) {
        cudaSetDevice(i);
        size_t free, total;
        if (cudaMemGetInfo(&free, &total) == cudaSuccess) {
            if (i == 0) g_vramTotal = total;
            size_t budget = (maxVramBudget > 0) ? maxVramBudget : (free * 80 / 100);
            std::cout << "[UMC] GPU " << i << " VRAM budget: " << (budget / (1024*1024)) << " MB\n";
        }
    }

    g_initialized = true;
    std::cout << "[UMC] Allocator initialized - " << g_deviceCount << " GPU(s)\n";
}

void* umc_alloc(size_t size, int device, cudaStream_t stream) {
    if (size == 0) return nullptr;

    if (device < 0) device = 0;

    cudaError_t err = cudaSetDevice(device);
    if (err != cudaSuccess) return nullptr;

    void* ptr = nullptr;
    err = cudaMalloc(&ptr, size);
    if (err != cudaSuccess) {
        std::cerr << "[UMC WARN] cudaMalloc(" << size << ") failed on GPU " << device << ": "
                  << cudaGetErrorString(err) << "\n";
        return nullptr;
    }

    {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_allocations[ptr] = size;
        g_totalAllocated += size;
        if (g_totalAllocated > g_peakAllocated) {
            g_peakAllocated = g_totalAllocated;
        }
    }

    return ptr;
}

void umc_free(void* ptr, size_t size, int device, cudaStream_t stream) {
    if (!ptr) return;

    cudaError_t err = cudaFree(ptr);

    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_allocations.find(ptr);
        if (it != g_allocations.end()) {
            g_totalAllocated -= it->second;
            g_allocations.erase(it);
        }
    }

    if (err != cudaSuccess) {
        std::cerr << "[UMC WARN] cudaFree failed: " << cudaGetErrorString(err) << "\n";
    }
}

void umc_cleanup_allocator() {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_allocations.clear();
    g_totalAllocated = 0;
    g_peakAllocated = 0;
    g_initialized = false;
}

} // extern "C"
