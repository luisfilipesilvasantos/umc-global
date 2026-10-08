#include "VirtualGpuMemory.h"
#include "nvml_mock.h"
#include "cuda_runtime_mock.h"
#include <windows.h>
#include <psapi.h>
#include <algorithm>
#include <iomanip>
#include <thread>
#include <sstream>
#include <iostream>
#include <vector>
#include <map>
#include <unordered_map>
#include <deque>
#include <string>
#include <mutex>
#include <cstdint>
#include <cmath>

// ---------------------------------------------------------
// Definições em falta (Normalmente estariam no .h)
// ---------------------------------------------------------

// ---------------------------------------------------------
// Implementação
// ---------------------------------------------------------

std::string tierToString(MemoryTier tier) {
    switch (tier) {
        case MemoryTier::Vram:     return "VRAM";
        case MemoryTier::Ram:      return "RAM";
        case MemoryTier::Pagefile: return "Pagefile";
    }
    return "Unknown";
}

VirtualGpuMemory::VirtualGpuMemory(
    size_t blockSizeBytes,
    size_t maxVramBudget,
    size_t maxRamBudget,
    size_t maxPagefileBudget
)
: m_blockSizeBytes(blockSizeBytes)
, m_totalVirtualCapacity(0)
, m_totalHostRam(0)
, m_availableHostRam(0)
, m_totalPagefile(0)
, m_availablePagefile(0)
, m_hPagefileMapping(nullptr)
, m_pagefileAllocatedBytes(0)
, m_pagefileLimitBytes(maxPagefileBudget)
, m_nvmlInitialized(false)
, m_hostAllocatedBytes(0)
, m_ramLimitBytes(maxRamBudget)
, m_maxVramBudgetOverride(maxVramBudget)
, m_maxRamBudgetOverride(maxRamBudget)
, m_maxPagefileBudgetOverride(maxPagefileBudget)
, m_globalAccessCounter(0)
, m_historyWindowSize(100)
, m_migrationHistoryWindowSize(100)
{
}

VirtualGpuMemory::~VirtualGpuMemory() {
    cleanup();
}

bool VirtualGpuMemory::initialize() {
    // 1. NVML
    nvmlReturn_t nvmlRes = nvmlInit();
    if (nvmlRes != NVML_SUCCESS) {
        std::cerr << "[UMC WARNING] NVML init failed: " << nvmlRes << ". GPU-less mode.\n";
        m_nvmlInitialized = false;
    } else {
        m_nvmlInitialized = true;
        unsigned int deviceCount = 0;
        nvmlRes = nvmlDeviceGetCount(&deviceCount);
        if (nvmlRes == NVML_SUCCESS) {
            for (unsigned int i = 0; i < deviceCount; ++i) {
                nvmlDevice_t device;
                if (nvmlDeviceGetHandleByIndex(i, &device) != NVML_SUCCESS) continue;
                char name[96] = {0};
                std::string deviceName = "Unknown GPU";
                if (nvmlDeviceGetName(device, name, sizeof(name)) == NVML_SUCCESS)
                    deviceName = name;

                nvmlMemory_t memInfo{};
                size_t totalVram = 0, freeVram = 0;
                if (nvmlDeviceGetMemoryInfo(device, &memInfo) == NVML_SUCCESS) {
                    totalVram = memInfo.total;
                    freeVram = memInfo.free;
                }
                m_gpuDevices.push_back({ static_cast<int>(i), deviceName, totalVram, freeVram });
                std::cout << "[UMC INFO] GPU " << i << ": " << deviceName
                          << " | VRAM: " << totalVram / (1024*1024) << " MB Free: " << freeVram / (1024*1024) << " MB\n";
            }
        } else {
            std::cerr << "[UMC WARNING] NVML Device Count failed: " << nvmlRes << "\n";
        }
    }

    // 2. System RAM
    MEMORYSTATUSEX memStatus{};
    memStatus.dwLength = sizeof(memStatus);
    if (GlobalMemoryStatusEx(&memStatus)) {
        m_totalHostRam = memStatus.ullTotalPhys;
        m_availableHostRam = memStatus.ullAvailPhys;
        std::cout << "[UMC INFO] System RAM: Total = " << m_totalHostRam / (1024*1024) << " MB"
                  << " | Available = " << m_availableHostRam / (1024*1024) << " MB\n";
    } else {
        std::cerr << "[UMC ERROR] GlobalMemoryStatusEx failed. Error: " << GetLastError() << "\n";
        return false;
    }

    // 3. Pagefile
    PERFORMANCE_INFORMATION perfInfo{};
    perfInfo.cb = sizeof(perfInfo);
    if (GetPerformanceInfo(&perfInfo, sizeof(perfInfo))) {
        size_t pageSize = perfInfo.PageSize;
        m_totalPagefile = perfInfo.CommitLimit * pageSize;
        m_availablePagefile = (perfInfo.CommitLimit - perfInfo.CommitTotal) * pageSize;
        std::cout << "[UMC INFO] Pagefile: Total = " << m_totalPagefile / (1024*1024) << " MB"
                  << " | Available = " << m_availablePagefile / (1024*1024) << " MB\n";
    } else {
        std::cerr << "[UMC ERROR] GetPerformanceInfo failed. Error: " << GetLastError() << "\n";
        return false;
    }

    // 4. Create pagefile mapping (bump pointer allocator)
    m_pagefileLimitBytes = maxPagefileBudget > 0 ? maxPagefileBudget : m_totalPagefile;
    if (m_pagefileLimitBytes == 0) {
        std::cerr << "[UMC ERROR] Pagefile budget is zero.\n";
        return false;
    }

    // Create pagefile mapping file
    HANDLE hFile = CreateFile(
        L"pagefile_mapping.dat",
        GENERIC_READ | GENERIC_WRITE,
        0, nullptr, CREATE_ALWAYS,
        FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE,
        nullptr
    );
    if (hFile == INVALID_HANDLE_VALUE) {
        std::cerr << "[UMC ERROR] Failed to create pagefile mapping file. Error: " << GetLastError() << "\n";
        return false;
    }

    // Set file size
    LARGE_INTEGER fileSize{};
    fileSize.QuadPart = static_cast<LONGLONG>(m_pagefileLimitBytes);
    if (!SetFilePointerEx(hFile, fileSize, nullptr, FILE_BEGIN) ||
        !SetEndOfFile(hFile)) {
        std::cerr << "[UMC ERROR] Failed to set pagefile size. Error: " << GetLastError() << "\n";
        CloseHandle(hFile);
        return false;
    }

    // Create mapping
    m_hPagefileMapping = CreateFileMapping(
        hFile,
        nullptr,
        PAGE_READWRITE,
        fileSize.HighPart,
        fileSize.LowPart,
        nullptr
    );
    CloseHandle(hFile);  // Mapping keeps it alive

    if (!m_hPagefileMapping) {
        std::cerr << "[UMC ERROR] Failed to create pagefile mapping. Error: " << GetLastError() << "\n";
        return false;
    }

    m_totalVirtualCapacity = m_ramLimitBytes + m_pagefileLimitBytes;
    for (auto& dev : m_gpuDevices) {
        m_totalVirtualCapacity += dev.totalVram;
    }

    std::cout << "[UMC INFO] VirtualGpuMemory initialized. Total capacity: "
              << m_totalVirtualCapacity / (1024*1024) << " MB\n";
    return true;
}

void VirtualGpuMemory::cleanup() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);

    // Free all blocks in blockMap
    for (auto& [blockId, block] : m_blockMap) {
        if (!block.isPinned && block.physicalOffset != 0) {
            switch (block.tier) {
                case MemoryTier::Vram:
                    cudaFree(reinterpret_cast<void*>(block.physicalOffset));
                    break;
                case MemoryTier::Ram:
                    VirtualFree(reinterpret_cast<void*>(block.physicalOffset), 0, MEM_RELEASE);
                    break;
                case MemoryTier::Pagefile:
                    // No explicit free needed; bump pointer leaves "holes"
                    break;
            }
        }
    }

    if (m_hPagefileMapping) {
        CloseHandle(m_hPagefileMapping);
        m_hPagefileMapping = nullptr;
    }

    if (m_nvmlInitialized) {
        nvmlShutdown();
        m_nvmlInitialized = false;
    }
}
