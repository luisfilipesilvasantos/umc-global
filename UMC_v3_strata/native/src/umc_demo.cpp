#include "umc_api.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main() {
    printf("========================================================\n");
    printf("   Unified Memory Controller (UMC) - Windows & CUDA\n");
    printf("========================================================\n\n");

    // 1. Initialize UMC
    UMC_InitParams params;
    params.blockSizeBytes = 4 * 1024 * 1024;     // 4MB blocks
    params.maxVramBudget = 1024 * 1024 * 1024;   // 1GB VRAM budget
    params.maxRamBudget = 1024 * 1024 * 1024;    // 1GB RAM budget
    params.maxPagefileBudget = 256 * 1024 * 1024; // 256MB pagefile

    printf("[INIT] Initializing Unified Memory Controller...\n");
    UMC_Handle hUmc = UMC_Init(&params);
    if (!hUmc) {
        printf("[FAIL] UMC_Init failed!\n");
        return 1;
    }
    printf("[OK] UMC initialized successfully\n\n");

    // 2. Query system info
    printf("=== System Information ===\n");
    printf("  Total capacity:       %zu MB\n", UMC_GetTotalVirtualCapacity(hUmc) / (1024 * 1024));
    printf("  Total host RAM:       %zu MB\n", UMC_GetTotalHostRam(hUmc) / (1024 * 1024));
    printf("  Available host RAM:   %zu MB\n", UMC_GetAvailableHostRam(hUmc) / (1024 * 1024));
    printf("  Total pagefile:       %zu MB\n", UMC_GetTotalPagefile(hUmc) / (1024 * 1024));
    printf("  Available pagefile:   %zu MB\n\n", UMC_GetAvailablePagefile(hUmc) / (1024 * 1024));

    // 3. Query GPU devices
    size_t gpuCount = UMC_GetGpuDeviceCount(hUmc);
    printf("=== GPU Devices (%zu) ===\n", gpuCount);
    for (size_t i = 0; i < gpuCount; i++) {
        UMC_GpuDeviceInfo info;
        if (UMC_GetGpuDeviceInfo(hUmc, i, &info)) {
            printf("  GPU %d: %s\n", info.id, info.name);
            printf("    VRAM Total: %zu MB\n", info.totalVram / (1024 * 1024));
            printf("    VRAM Free:  %zu MB\n", info.freeVram / (1024 * 1024));
        }
    }
    printf("\n");

    // 4. Write and read test
    printf("=== Memory Test ===\n");
    size_t testSize = 4096; // 4KB test
    char* testData = (char*)malloc(testSize);
    if (!testData) { printf("[FAIL] malloc failed\n"); UMC_Destroy(hUmc); return 1; }
    
    for (size_t i = 0; i < testSize; i++) testData[i] = (char)(i % 256);
    
    printf("[WRITE] Writing %zu bytes to block 0...\n", testSize);
    if (!UMC_WriteBlock(hUmc, 0, testData, testSize)) {
        printf("[FAIL] UMC_WriteBlock failed\n");
        free(testData); UMC_Destroy(hUmc); return 1;
    }
    printf("[OK] Write successful\n");

    char* readData = (char*)malloc(testSize);
    if (!readData) { printf("[FAIL] malloc failed\n"); free(testData); UMC_Destroy(hUmc); return 1; }
    memset(readData, 0, testSize);
    
    printf("[READ] Reading back %zu bytes from block 0...\n", testSize);
    if (!UMC_ReadBlock(hUmc, 0, readData, testSize)) {
        printf("[FAIL] UMC_ReadBlock failed\n");
        free(testData); free(readData); UMC_Destroy(hUmc); return 1;
    }
    printf("[OK] Read successful\n");

    // Verify data
    int match = 1;
    for (size_t i = 0; i < testSize; i++) {
        if (testData[i] != readData[i]) { match = 0; break; }
    }
    printf("[%s] Data verification: %s\n", match ? "OK" : "FAIL", match ? "data matches" : "data mismatch");
    
    // 5. Block location
    UMC_BlockLocation loc;
    if (UMC_GetBlockLocation(hUmc, 0, &loc)) {
        const char* tierNames[] = {"VRAM", "RAM", "Pagefile"};
        printf("[INFO] Block 0 is in %s (device %d, offset 0x%zx)\n",
               tierNames[loc.tier], loc.deviceId, loc.physicalOffset);
    }

    free(testData);
    free(readData);

    // 6. Cleanup
    printf("\n=== Cleanup ===\n");
    UMC_Destroy(hUmc);
    printf("[OK] UMC destroyed successfully\n");

    printf("\n=== DEMO COMPLETED SUCCESSFULLY ===\n");
    return 0;
}
