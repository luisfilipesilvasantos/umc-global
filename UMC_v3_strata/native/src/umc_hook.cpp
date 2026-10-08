#include <windows.h>
#include <psapi.h>
#include <cuda.h>
#include <cuda_runtime.h>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <vector>
#include "allocator/allocator.h"

// ============================================================================
// IAT Patching - nvcuda.dll exports are RIP-relative IAT thunks (ff 25 xx xx xx xx)
// ============================================================================

static bool isIatThunk(void* addr) {
    if (!addr) return false;
    uint8_t* code = (uint8_t*)addr;
    return (code[0] == 0xFF && code[1] == 0x25);
}

static void* resolveIatTarget(void* thunkAddr, void** outIatEntry) {
    uint8_t* code = (uint8_t*)thunkAddr;
    int32_t offset = *(int32_t*)(code + 2);
    void** iatEntry = (void**)((uint64_t)thunkAddr + 6 + offset);
    if (outIatEntry) *outIatEntry = (void*)iatEntry;
    return *iatEntry;
}

static bool patchIatEntry(void* iatEntry, void* hookFunc, void** originalOut) {
    void* original = *(void**)iatEntry;
    if (originalOut) *originalOut = original;

    DWORD oldProtect = 0;
    if (!VirtualProtect(iatEntry, sizeof(void*), PAGE_READWRITE, &oldProtect)) {
        printf("[UMC]   VirtualProtect(IAT) failed: %lu\n", GetLastError());
        return false;
    }

    *(void**)iatEntry = hookFunc;
    VirtualProtect(iatEntry, sizeof(void*), oldProtect, &oldProtect);
    FlushInstructionCache(GetCurrentProcess(), iatEntry, sizeof(void*));
    printf("[UMC]   IAT %p: %p -> %p\n", iatEntry, original, hookFunc);
    return true;
}

// ============================================================================
// CUDA Driver API type aliases
// ============================================================================

typedef CUresult (CUDAAPI *cuMemAlloc_t)(CUdeviceptr*, size_t);
typedef CUresult (CUDAAPI *cuMemFree_t)(CUdeviceptr);
typedef CUresult (CUDAAPI *cuMemGetInfo_t)(size_t*, size_t*);
typedef CUresult (CUDAAPI *cuMemCreate_t)(CUmemGenericAllocationHandle*, size_t, const CUmemAllocationProp*, unsigned long long);
typedef CUresult (CUDAAPI *cuMemMap_t)(CUdeviceptr, size_t, size_t, CUmemGenericAllocationHandle, unsigned long long);
typedef CUresult (CUDAAPI *cuMemAllocAsync_t)(CUdeviceptr*, size_t, CUstream);

// Original function pointers
static cuMemAlloc_t Real_cuMemAlloc_v2 = nullptr;
static cuMemFree_t Real_cuMemFree_v2 = nullptr;
static cuMemGetInfo_t Real_cuMemGetInfo_v2 = nullptr;
static cuMemAlloc_t Real_cuMemAlloc = nullptr;
static cuMemFree_t Real_cuMemFree = nullptr;
static cuMemGetInfo_t Real_cuMemGetInfo = nullptr;
static cuMemCreate_t Real_cuMemCreate = nullptr;
static cuMemMap_t Real_cuMemMap = nullptr;
static cuMemAllocAsync_t Real_cuMemAllocAsync = nullptr;

// ============================================================================
// Allocation Tracking
// ============================================================================

struct TrackedAlloc {
    void* ptr;
    size_t size;
    uint64_t timestamp;
};

static std::map<void*, TrackedAlloc> g_allocs;
static std::mutex g_allocsMutex;
static size_t g_totalAllocated = 0;
static size_t g_peakAllocated = 0;
static size_t g_vramTotal = 0;
static size_t g_vramFree = 0;
static uint64_t g_tickCounter = 0;
static bool g_hooksActive = false;

static void trackAlloc(void* ptr, size_t size) {
    if (!ptr || size == 0) return;
    std::lock_guard<std::mutex> lock(g_allocsMutex);
    g_allocs[ptr] = { ptr, size, ++g_tickCounter };
    g_totalAllocated += size;
    if (g_totalAllocated > g_peakAllocated)
        g_peakAllocated = g_totalAllocated;
}

static void trackFree(void* ptr) {
    if (!ptr) return;
    std::lock_guard<std::mutex> lock(g_allocsMutex);
    auto it = g_allocs.find(ptr);
    if (it != g_allocs.end()) {
        g_totalAllocated -= it->second.size;
        g_allocs.erase(it);
    }
}

// ============================================================================
// Hook Wrappers
// ============================================================================

/* Wrapper driver para a ladder (0=ok, 1=OOM, 2=erro real). */
static int driver_alloc_fn(void* /*ctx*/, size_t size, void** out) {
    CUresult res = Real_cuMemAlloc_v2((CUdeviceptr*)out, size);
    if (res == CUDA_SUCCESS) return 0;
    if (res == CUDA_ERROR_OUT_OF_MEMORY) return 1;
    return 2;
}

static CUresult CUDAAPI Hook_cuMemAlloc_v2(CUdeviceptr* dptr, size_t bytesize) {
    /* OOM-retry ladder (Fase 1): em OOM sincroniza + backoff + retry. */
    UmC_RetryConfig cfg = {4, 25, 1};
    int ladder_err = 0;
    void* ptr = umc_alloc_with_retry(driver_alloc_fn, nullptr, bytesize, &cfg, &ladder_err);
    if (ptr) {
        *dptr = (CUdeviceptr)ptr;
        if (g_hooksActive) trackAlloc(ptr, bytesize);
        return CUDA_SUCCESS;
    }
    /* Esgotado — devolver o erro REAL (OOM ou erro) ao chamador. */
    return (ladder_err == 2) ? CUDA_ERROR_UNKNOWN : CUDA_ERROR_OUT_OF_MEMORY;
}

static CUresult CUDAAPI Hook_cuMemFree_v2(CUdeviceptr dptr) {
    if (g_hooksActive) trackFree((void*)dptr);
    return Real_cuMemFree_v2(dptr);
}

static CUresult CUDAAPI Hook_cuMemGetInfo_v2(size_t* free, size_t* total) {
    CUresult res = Real_cuMemGetInfo_v2(free, total);
    if (res == CUDA_SUCCESS) {
        g_vramTotal = *total;   // estado fisico real (para stats)
        g_vramFree = *free;
        /* Se o orcamento virtual esta ativo, devolve a VISTA VIRTUAL
         * (o "mentir" anti-OOM): torch ve o pool, nao a fisica. O estado
         * fisico real do UMC vem do NVML (VirtualGpuMemory.cpp), nao
         * daqui — inflar este hook nao corrompe a gestao interna. */
        size_t vfree, vtotal;
        if (umc_get_virtual_view(&vfree, &vtotal) == 1) {
            *free = vfree;
            *total = vtotal;
        }
    }
    return res;
}

static CUresult CUDAAPI Hook_cuMemAlloc(CUdeviceptr* dptr, size_t bytesize) {
    return Hook_cuMemAlloc_v2(dptr, bytesize);
}

static CUresult CUDAAPI Hook_cuMemFree(CUdeviceptr dptr) {
    return Hook_cuMemFree_v2(dptr);
}

static CUresult CUDAAPI Hook_cuMemGetInfo(size_t* free, size_t* total) {
    return Hook_cuMemGetInfo_v2(free, total);
}

// Track cuMemCreate + cuMemMap (Virtual Memory API, used by CUDA 12+)
struct PendingMap {
    CUmemGenericAllocationHandle handle;
    size_t size;
};
static std::map<CUmemGenericAllocationHandle, PendingMap> g_pendingCreates;

static CUresult CUDAAPI Hook_cuMemCreate(CUmemGenericAllocationHandle* handle, size_t size,
                                          const CUmemAllocationProp* prop, unsigned long long flags) {
    CUresult res = Real_cuMemCreate(handle, size, prop, flags);
    if (res == CUDA_SUCCESS && g_hooksActive) {
        std::lock_guard<std::mutex> lock(g_allocsMutex);
        g_pendingCreates[*handle] = { *handle, size };
    }
    return res;
}

static CUresult CUDAAPI Hook_cuMemMap(CUdeviceptr dptr, size_t size, size_t offset,
                                       CUmemGenericAllocationHandle handle, unsigned long long flags) {
    CUresult res = Real_cuMemMap(dptr, size, offset, handle, flags);
    if (res == CUDA_SUCCESS && g_hooksActive) {
        std::lock_guard<std::mutex> lock(g_allocsMutex);
        auto it = g_pendingCreates.find(handle);
        if (it != g_pendingCreates.end()) {
            trackAlloc((void*)dptr, it->second.size);
            g_pendingCreates.erase(it);
        } else {
            trackAlloc((void*)dptr, size);
        }
    }
    return res;
}

static CUresult CUDAAPI Hook_cuMemAllocAsync(CUdeviceptr* dptr, size_t bytesize, CUstream hStream) {
    CUresult res = Real_cuMemAllocAsync(dptr, bytesize, hStream);
    if (res == CUDA_SUCCESS && g_hooksActive)
        trackAlloc((void*)*dptr, bytesize);
    return res;
}

// ============================================================================
// Exported API
// ============================================================================

extern "C" {

__declspec(dllexport) int umc_install_hooks() {
    if (g_hooksActive) {
        printf("[UMC] Hooks already installed\n");
        return 0;
    }

    HMODULE hNvcuda = GetModuleHandleA("nvcuda.dll");
    if (!hNvcuda) hNvcuda = LoadLibraryA("nvcuda.dll");
    if (!hNvcuda) return -1;

    struct FuncToHook {
        const char* name;
        void* hookFunc;
        void** realOut;
        void* iatEntry;
        void* original;
    };

    FuncToHook targets[] = {
        { "cuMemAlloc_v2",   (void*)Hook_cuMemAlloc_v2,   (void**)&Real_cuMemAlloc_v2,   nullptr, nullptr },
        { "cuMemFree_v2",    (void*)Hook_cuMemFree_v2,    (void**)&Real_cuMemFree_v2,    nullptr, nullptr },
        { "cuMemGetInfo_v2", (void*)Hook_cuMemGetInfo_v2, (void**)&Real_cuMemGetInfo_v2, nullptr, nullptr },
        { "cuMemAlloc",      (void*)Hook_cuMemAlloc,      (void**)&Real_cuMemAlloc,      nullptr, nullptr },
        { "cuMemFree",       (void*)Hook_cuMemFree,       (void**)&Real_cuMemFree,       nullptr, nullptr },
        { "cuMemGetInfo",    (void*)Hook_cuMemGetInfo,    (void**)&Real_cuMemGetInfo,    nullptr, nullptr },
        { "cuMemCreate",     (void*)Hook_cuMemCreate,     (void**)&Real_cuMemCreate,     nullptr, nullptr },
        { "cuMemMap",        (void*)Hook_cuMemMap,        (void**)&Real_cuMemMap,        nullptr, nullptr },
        { "cuMemAllocAsync", (void*)Hook_cuMemAllocAsync, (void**)&Real_cuMemAllocAsync, nullptr, nullptr },
    };

    int count = 0;
    for (auto& t : targets) {
        void* thunk = GetProcAddress(hNvcuda, t.name);
        if (!thunk || !isIatThunk(thunk)) continue;
        void* realFunc = resolveIatTarget(thunk, &t.iatEntry);
        if (patchIatEntry(t.iatEntry, t.hookFunc, &t.original)) {
            *(t.realOut) = (decltype(Real_cuMemAlloc_v2))t.original;
            count++;
        }
    }

    g_hooksActive = true;
    return count;
}

__declspec(dllexport) int umc_uninstall_hooks() {
    g_hooksActive = false;
    printf("[UMC] Hooks deactivated\n");
    return 0;
}

__declspec(dllexport) int umc_get_tracked_count() {
    std::lock_guard<std::mutex> lock(g_allocsMutex);
    return (int)g_allocs.size();
}

__declspec(dllexport) size_t umc_get_total_allocated() {
    std::lock_guard<std::mutex> lock(g_allocsMutex);
    return g_totalAllocated;
}

__declspec(dllexport) size_t umc_get_peak_allocated() {
    std::lock_guard<std::mutex> lock(g_allocsMutex);
    return g_peakAllocated;
}

__declspec(dllexport) void umc_reset_peak() {
    std::lock_guard<std::mutex> lock(g_allocsMutex);
    g_peakAllocated = g_totalAllocated;
}

__declspec(dllexport) void umc_print_stats() {
    std::lock_guard<std::mutex> lock(g_allocsMutex);
    printf("[UMC] === UMC Hook Stats ===\n");
    printf("[UMC]   Active allocations: %zu\n", g_allocs.size());
    printf("[UMC]   Total allocated: %zu MB\n", g_totalAllocated / (1024 * 1024));
    printf("[UMC]   Peak allocated:  %zu MB\n", g_peakAllocated / (1024 * 1024));
    printf("[UMC]   VRAM total:      %zu MB\n", g_vramTotal / (1024 * 1024));
    printf("[UMC]   VRAM free:       %zu MB\n", g_vramFree / (1024 * 1024));
    printf("[UMC] =====================\n");
}

/* ============================================================================
 * Contrato de producao (dual exports) — o bridge v3 chama umc_hook_install.
 * ==========================================================================*/

__declspec(dllexport) int umc_hook_install(size_t vram_gb, size_t ram_gb,
                                            size_t pagefile_gb, int /*prio*/) {
    umc_set_virtual_budget(vram_gb * 1024ULL * 1024 * 1024,
                           ram_gb * 1024ULL * 1024 * 1024,
                           pagefile_gb * 1024ULL * 1024 * 1024);
    int n = umc_install_hooks();
    return (n >= 0) ? 0 : -1;
}

__declspec(dllexport) int umc_hook_is_active() {
    return g_hooksActive ? 1 : 0;
}

__declspec(dllexport) int umc_hook_uninstall() {
    return umc_uninstall_hooks();
}

/* Vista virtual da memoria (mesma que o hook de cuMemGetInfo devolve).
 * Exportada para compatibilidade com o contrato de producao. */
__declspec(dllexport) cudaError_t umc_hooked_cudaMemGetInfo(size_t* free, size_t* total) {
    size_t f, t;
    if (umc_get_virtual_view(&f, &t) == 1) {
        *free = f;
        *total = t;
        return cudaSuccess;
    }
    return cudaMemGetInfo(free, total);  // sem orcamento -> valores reais
}

}  // extern "C"
