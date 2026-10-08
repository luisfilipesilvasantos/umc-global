#include "VirtualGpuMemory.h"
#include "allocator/allocator.h"
#include <cuda_runtime.h>
#include <mutex>

static bool g_initialized = false;
static std::mutex g_mutex;
static int g_deviceCount = 0;
static size_t g_vramTotal = 0;

extern "C" {

__declspec(dllexport) void umc_init_allocator(size_t maxVramBudget, size_t maxRamBudget) {
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

__declspec(dllexport) void* umc_alloc(size_t size, int device, cudaStream_t stream) {
    if (size == 0) return nullptr;

    if (device < 0) device = 0;

    cudaError_t err = cudaSetDevice(device);
    if (err != cudaSuccess) return nullptr;

    void* ptr = nullptr;
    err = cudaMalloc(&ptr, size);
    if (err != cudaSuccess) {
        /* O OOM-retry ladder vive no hook (umc_hook_v2.dll) — este cudaMalloc
         * passa pelo hook de cuMemAlloc, que faz sync+backoff+retry antes de
         * devolver o erro real. Nunca engole o erro. */
        std::cerr << "[UMC WARN] cudaMalloc(" << size << ") failed on GPU " << device << ": "
                  << cudaGetErrorString(err) << "\n";
        return nullptr;
    }

    umc_track_alloc(ptr, size);
    return ptr;
}

__declspec(dllexport) void umc_free(void* ptr, size_t size, int device, cudaStream_t stream) {
    if (!ptr) return;

    cudaError_t err = cudaFree(ptr);
    umc_track_free(ptr);

    if (err != cudaSuccess) {
        std::cerr << "[UMC WARN] cudaFree failed: " << cudaGetErrorString(err) << "\n";
    }
}

__declspec(dllexport) void umc_cleanup_allocator() {
    umc_track_reset();
    g_initialized = false;
}

} // extern "C"
