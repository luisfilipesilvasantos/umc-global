#include "umc_api.h"
#include "VirtualGpuMemory.h"

// Estrutura de contexto para armazenar instância da classe C++
typedef struct {
    VirtualGpuMemory* vgm;
} UMC_Context;

extern "C" {

UMC_Handle UMC_Init(const UMC_InitParams* params) {
    if (!params) return nullptr;
    
    try {
        // Criar instância da classe VirtualGpuMemory com os parâmetros fornecidos
        VirtualGpuMemory* vgm = new VirtualGpuMemory(
            params->blockSizeBytes,
            params->maxVramBudget,
            params->maxRamBudget,
            params->maxPagefileBudget
        );
        
        // Inicializar a instância
        if (!vgm->initialize()) {
            delete vgm;
            return nullptr;
        }
        
        // Criar contexto e retornar handle
        UMC_Context* ctx = new UMC_Context();
        ctx->vgm = vgm;
        return static_cast<UMC_Handle>(ctx);
    } catch (...) {
        return nullptr;
    }
}

void UMC_Destroy(UMC_Handle handle) {
    if (!handle) return;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    if (ctx->vgm) {
        delete ctx->vgm;
    }
    delete ctx;
}

size_t UMC_GetGpuDeviceCount(UMC_Handle handle) {
    if (!handle) return 0;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    const std::vector<GpuDeviceInfo>& devices = ctx->vgm->getGpuDevices();
    return devices.size();
}

BOOL UMC_GetGpuDeviceInfo(UMC_Handle handle, size_t index, UMC_GpuDeviceInfo* outInfo) {
    if (!handle || !outInfo) return FALSE;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    const std::vector<GpuDeviceInfo>& devices = ctx->vgm->getGpuDevices();
    
    if (index >= devices.size()) return FALSE;
    
    const GpuDeviceInfo& dev = devices[index];
    outInfo->id = dev.id;
    strncpy_s(outInfo->name, sizeof(outInfo->name), dev.name.c_str(), _TRUNCATE);
    outInfo->totalVram = dev.totalVram;
    outInfo->freeVram = dev.freeVram;
    
    return TRUE;
}

void* umc_alloc(size_t size, int device, cudaStream_t stream);
void umc_free(void* ptr, size_t size, int device, cudaStream_t stream);

size_t UMC_GetTotalVirtualCapacity(UMC_Handle handle) {
    if (!handle) return 0;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->getTotalVirtualCapacity();
}

size_t UMC_GetTotalHostRam(UMC_Handle handle) {
    if (!handle) return 0;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->getTotalHostRam();
}

size_t UMC_GetAvailableHostRam(UMC_Handle handle) {
    if (!handle) return 0;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->getAvailableHostRam();
}

size_t UMC_GetTotalPagefile(UMC_Handle handle) {
    if (!handle) return 0;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->getTotalPagefile();
}

size_t UMC_GetAvailablePagefile(UMC_Handle handle) {
    if (!handle) return 0;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->getAvailablePagefile();
}

BOOL UMC_WriteBlock(UMC_Handle handle, size_t blockId, const void* data, size_t size) {
    if (!handle || !data) return FALSE;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->writeBlock(blockId, data, size);
}

BOOL UMC_ReadBlock(UMC_Handle handle, size_t blockId, void* outData, size_t size) {
    if (!handle || !outData) return FALSE;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->readBlock(blockId, outData, size);
}

BOOL UMC_GetBlockLocation(UMC_Handle handle, size_t blockId, UMC_BlockLocation* outLocation) {
    if (!handle || !outLocation) return FALSE;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    BlockLocation loc;
    BOOL result = ctx->vgm->getBlockLocation(blockId, loc);
    
    if (result) {
        outLocation->tier = static_cast<int>(loc.tier);
        outLocation->deviceId = loc.deviceId;
        outLocation->physicalOffset = loc.physicalOffset;
        outLocation->size = loc.size;
    }
    
    return result;
}

BOOL UMC_MigrateBlockToTier(UMC_Handle handle, size_t blockId, int targetTier, int targetDeviceId) {
    if (!handle) return FALSE;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    MemoryTier tier = static_cast<MemoryTier>(targetTier);
    return ctx->vgm->migrateBlockToTier(blockId, tier, targetDeviceId);
}

void UMC_RegisterAccess(UMC_Handle handle, size_t blockId) {
    if (!handle) return;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    ctx->vgm->registerAccess(blockId);
}

BOOL UMC_IsThrashingDetected(UMC_Handle handle) {
    if (!handle) return FALSE;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->isThrashingDetected() ? TRUE : FALSE;
}

BOOL UMC_GetTierStats(UMC_Handle handle, UMC_TierStats* outStats) {
    if (!handle || !outStats) return FALSE;
    
    UMC_Context* ctx = static_cast<UMC_Context*>(handle);
    return ctx->vgm->getTierStats(outStats);
}

// Funções para PyTorch CUDAPluggableAllocator: implementadas em umc_pytorch_allocator.cpp

} // extern "C"
