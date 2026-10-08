#pragma once
#include <cstddef>

/* ============================================================================
 * UMC Fase 1 — allocator partilhado: OOM-retry ladder + soft-OOM por NVML.
 *
 * Compilado no allocator_v2.dll (dono do estado). O umc_hook_v2.dll liga
 * contra este header (dllimport) e consulta umc_get_virtual_view() /
 * umc_alloc_with_retry() — o estado vive numa so DLL.
 *
 * Contrato de producao (dual exports): umc_alloc / umc_free /
 * umc_init_allocator / umc_cleanup_allocator tem de existir no
 * allocator_v2.dll — sao o que o bridge v3 (CUDAPluggableAllocator) consome.
 * ==========================================================================*/

#ifdef UMC_ALLOC_BUILD
  #define UMC_ALLOC_API __declspec(dllexport)
#else
  #define UMC_ALLOC_API __declspec(dllimport)
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int max_attempts;      /* total de tentativas (1 = sem retry) */
    int backoff_ms_base;   /* backoff linear: base * tentativa (ms) */
    int nvml_log_every;    /* regista pressao NVML a cada N tentativas (0 = nunca) */
} UmC_RetryConfig;

/* Callback de alocacao: devolve 0 = sucesso (*out valido),
 * 1 = OOM (retryable), outro = erro real (nao engolir). */
typedef int (*UmC_AllocFn)(void* ctx, size_t size, void** out);

/* Ladder de retry: tenta fn(ctx, size); em OOM sincroniza, espera (backoff)
 * e tenta de novo. Devolve nullptr se esgotar. out_err (opcional):
 * 0 = sucesso, 1 = OOM esgotado, 2 = erro real. O erro real fica sempre
 * disponivel para o chamador propagar (regra: nunca engolir erros). */
UMC_ALLOC_API void* umc_alloc_with_retry(UmC_AllocFn fn, void* ctx, size_t size,
                                          const UmC_RetryConfig* cfg, int* out_err);

/* Telemetria NVML (soft-OOM). 0 / -1.0 = NVML indisponivel. */
UMC_ALLOC_API size_t umc_nvml_physical_free(void);
UMC_ALLOC_API size_t umc_nvml_physical_total(void);
UMC_ALLOC_API double umc_physical_pressure_ratio(void);

/* Orcamento virtual (definido por umc_hook_install). 0 = nao inicializado. */
UMC_ALLOC_API void umc_set_virtual_budget(size_t vram_bytes, size_t ram_bytes, size_t pagefile_bytes);
UMC_ALLOC_API size_t umc_get_virtual_total(void);

/* Visao virtual da memoria (o "mentir" anti-OOM): total = orcamento,
 * free = orcamento - alocado. Devolve 1 se devolveu a vista virtual,
 * 0 se nao ha orcamento (o chamador usa os valores reais). */
UMC_ALLOC_API int umc_get_virtual_view(size_t* free_bytes, size_t* total_bytes);

/* Contabilidade de alocacoes (alimenta a vista virtual). */
UMC_ALLOC_API void umc_track_alloc(void* ptr, size_t size);
UMC_ALLOC_API void umc_track_free(void* ptr);
UMC_ALLOC_API size_t umc_get_tracked_total(void);
UMC_ALLOC_API void umc_track_reset(void);

#ifdef __cplusplus
}
#endif
