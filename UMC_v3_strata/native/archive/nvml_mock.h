#pragma once
#include <windows.h>

// Tipos e constantes NVML mockados para compilação sem CUDA SDK
typedef void* nvmlDevice_t;
typedef int nvmlReturn_t;

#define NVML_SUCCESS 0
#define NVML_ERROR_NOT_FOUND 1
#define NVML_ERROR_LIBRARY_NOT_FOUND 2
#define NVML_ERROR_FUNCTION_NOT_FOUND 3
#define NVML_ERROR_INCOMPATIBLE_LIBRARY_VERSION 4

// Funções stub para compilação sem NVML real
typedef struct nvmlDevice_s {
    int dummy;
} nvmlDevice_internal_t;

inline nvmlReturn_t nvmlInit() { return NVML_SUCCESS; }
inline nvmlReturn_t nvmlShutdown() { return NVML_SUCCESS; }
inline nvmlReturn_t nvmlDeviceGetCount(unsigned int* deviceCount) {
    *deviceCount = 0;
    return NVML_ERROR_NOT_FOUND;
}
inline nvmlReturn_t nvmlDeviceGetHandleByIndex(unsigned int index, nvmlDevice_t* device) {
    return NVML_ERROR_NOT_FOUND;
}
inline nvmlReturn_t nvmlDeviceGetName(nvmlDevice_t device, char* name, unsigned int length) {
    if (name && length > 0) name[0] = '\0';
    return NVML_ERROR_FUNCTION_NOT_FOUND;
}
struct nvmlMemory_st {
    unsigned long long total;
    unsigned long long free;
    unsigned long long used;
};
typedef struct nvmlMemory_st nvmlMemory_t;
inline nvmlReturn_t nvmlDeviceGetMemoryInfo(nvmlDevice_t device, nvmlMemory_t* memInfo) {
    if (memInfo) {
        memInfo->total = 0;
        memInfo->free = 0;
        memInfo->used = 0;
    }
    return NVML_ERROR_FUNCTION_NOT_FOUND;
}
