#define UMC_ALLOC_BUILD
#include "allocator.h"
#include <windows.h>
#include <cuda_runtime.h>
#include <nvml.h>
#include <mutex>
#include <unordered_map>
#include <cstdio>

/* ============================================================================
 * Estado partilhado (dono: allocator_v2.dll)
 * ==========================================================================*/

static std::mutex g_mutex;
static size_t g_vramBudget = 0;
static size_t g_ramBudget = 0;
static size_t g_pagefileBudget = 0;

/* Contabilidade de alocacoes (caminho torch: umc_alloc/umc_free). */
static std::unordered_map<void*, size_t> g_allocations;
static size_t g_trackedTotal = 0;

/* NVML lazy-init (uma vez, thread-safe). */
static bool g_nvmlOk = false;
static nvmlDevice_t g_nvmlDev = nullptr;
static std::once_flag g_nvmlOnce;

static void nvml_ensure() {
    std::call_once(g_nvmlOnce, []() {
        if (nvmlInit() != NVML_SUCCESS) return;
        if (nvmlDeviceGetHandleByIndex(0, &g_nvmlDev) != NVML_SUCCESS) {
            nvmlShutdown();
            return;
        }
        g_nvmlOk = true;
    });
}

static void log_soft_oom(size_t size, int attempt) {
    size_t free_b = umc_nvml_physical_free();
    size_t total_b = umc_nvml_physical_total();
    double ratio = umc_physical_pressure_ratio();
    std::fprintf(stderr,
        "[UMC SOFT-OOM] alocacao %zu MiB falhou (tentativa %d) | "
        "fisica livre %zu MiB / %zu MiB (%.1f%% ocupada) | "
        "virtual livre %zu MiB\n",
        size / (1024 * 1024), attempt,
        free_b / (1024 * 1024), total_b / (1024 * 1024), ratio * 100.0,
        (g_vramBudget > g_trackedTotal ? g_vramBudget - g_trackedTotal : 0) / (1024 * 1024));
}

/* ============================================================================
 * OOM-retry ladder
 * ==========================================================================*/

void* umc_alloc_with_retry(UmC_AllocFn fn, void* ctx, size_t size,
                           const UmC_RetryConfig* cfg, int* out_err) {
    if (out_err) *out_err = 0;
    UmC_RetryConfig c = cfg ? *cfg : UmC_RetryConfig{4, 25, 1};
    if (c.max_attempts < 1) c.max_attempts = 1;
    if (c.backoff_ms_base < 0) c.backoff_ms_base = 0;

    for (int attempt = 1; attempt <= c.max_attempts; ++attempt) {
        void* ptr = nullptr;
        int rc = fn(ctx, size, &ptr);
        if (rc == 0 && ptr) return ptr;
        if (rc != 1) {  // erro real: nao engolir, devolver ja
            if (out_err) *out_err = 2;
            return nullptr;
        }
        // OOM: ladder — sincroniza, regista pressao, espera, tenta de novo
        if (attempt < c.max_attempts) {
            cudaDeviceSynchronize();  // libertar trabalho em voo
            if (c.nvml_log_every > 0 && (attempt % c.nvml_log_every) == 0)
                log_soft_oom(size, attempt);
            if (c.backoff_ms_base > 0)
                Sleep(static_cast<DWORD>(c.backoff_ms_base * attempt));
        }
    }
    if (out_err) *out_err = 1;  // OOM esgotado
    return nullptr;
}

/* ============================================================================
 * Telemetria NVML (soft-OOM)
 * ==========================================================================*/

size_t umc_nvml_physical_free() {
    nvml_ensure();
    if (!g_nvmlOk) return 0;
    nvmlMemory_t mi;
    if (nvmlDeviceGetMemoryInfo(g_nvmlDev, &mi) != NVML_SUCCESS) return 0;
    return static_cast<size_t>(mi.free);
}

size_t umc_nvml_physical_total() {
    nvml_ensure();
    if (!g_nvmlOk) return 0;
    nvmlMemory_t mi;
    if (nvmlDeviceGetMemoryInfo(g_nvmlDev, &mi) != NVML_SUCCESS) return 0;
    return static_cast<size_t>(mi.total);
}

double umc_physical_pressure_ratio() {
    size_t total = umc_nvml_physical_total();
    if (total == 0) return -1.0;
    size_t free_b = umc_nvml_physical_free();
    return static_cast<double>(total - free_b) / static_cast<double>(total);
}

/* ============================================================================
 * Orcamento virtual + vista virtual (o "mentir" anti-OOM)
 * ==========================================================================*/

void umc_set_virtual_budget(size_t vram_bytes, size_t ram_bytes, size_t pagefile_bytes) {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_vramBudget = vram_bytes;
    g_ramBudget = ram_bytes;
    g_pagefileBudget = pagefile_bytes;
}

size_t umc_get_virtual_total() {
    std::lock_guard<std::mutex> lock(g_mutex);
    return g_vramBudget;
}

int umc_get_virtual_view(size_t* free_bytes, size_t* total_bytes) {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_vramBudget == 0) return 0;  // sem orcamento -> usar valores reais
    *total_bytes = g_vramBudget;
    *free_bytes = (g_trackedTotal >= g_vramBudget) ? 0 : (g_vramBudget - g_trackedTotal);
    return 1;
}

/* ============================================================================
 * Contabilidade
 * ==========================================================================*/

void umc_track_alloc(void* ptr, size_t size) {
    if (!ptr || size == 0) return;
    std::lock_guard<std::mutex> lock(g_mutex);
    g_allocations[ptr] = size;
    g_trackedTotal += size;
}

void umc_track_free(void* ptr) {
    if (!ptr) return;
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_allocations.find(ptr);
    if (it != g_allocations.end()) {
        g_trackedTotal -= it->second;
        g_allocations.erase(it);
    }
}

size_t umc_get_tracked_total() {
    std::lock_guard<std::mutex> lock(g_mutex);
    return g_trackedTotal;
}

void umc_track_reset() {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_allocations.clear();
    g_trackedTotal = 0;
}
