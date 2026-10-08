#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <string>
#include <vector>
#include <unordered_map>
#include <mutex>
#include <deque>
#include <iostream>
#include <windows.H>
#include "cuda_runtime_mock.h"

// Definições em falta (Normalmente estariam no .h)
enum class MemoryTier { Vram, Ram, Pagefile };

struct BlockLocation {
    MemoryTier tier = MemoryTier::Ram;
    int deviceId = -1;                // GPU index ou -1 para Host
    size_t physicalOffset = 0;        // Device Ptr (VRAM), Host Ptr (RAM), File Offset (Pagefile)
    size_t size = 0;
    bool isPinned = false;
    size_t lastAccessTime = 0;
    size_t residentSince = 0;
};

struct ResidencyHistoryEntry {
    MemoryTier tier;
    int deviceId;
    uint64_t timestamp;
};

struct GpuDeviceInfo {
    int id = -1;
    std::string name;
    size_t totalVram = 0;
    size_t freeVram = 0;
};

// Estruturas auxiliares para alocações de sistema (usadas no .cpp)
struct AllocationEntry {
    MemoryTier tier;
    int deviceId;
    size_t size;
    void* hostPtr;
    HANDLE hFileMapping;
};

struct SystemAllocationEntry {
    MemoryTier tier;
    int deviceId;
    void* hostPtr;
    HANDLE hFileMapping;
};

class VirtualGpuMemory {
public:
    explicit VirtualGpuMemory(
        size_t blockSizeBytes = 4 * 1024 * 1024,
        size_t maxVramBudget = 0,
        size_t maxRamBudget = 0,
        size_t maxPagefileBudget = 0
    );
    ~VirtualGpuMemory();

    VirtualGpuMemory(const VirtualGpuMemory&) = delete;
    VirtualGpuMemory& operator=(const VirtualGpuMemory&) = delete;
    VirtualGpuMemory(VirtualGpuMemory&&) = delete;
    VirtualGpuMemory& operator=(VirtualGpuMemory&&) = delete;

    bool initialize();
    size_t getBlockSize() const { return m_blockSizeBytes; }
    size_t getTotalVirtualCapacity() const { return m_totalVirtualCapacity; }

    const std::vector<GpuDeviceInfo>& getGpuDevices() const { return m_gpuDevices; }
    size_t getTotalHostRam() const { return m_totalHostRam; }
    size_t getAvailableHostRam() const { return m_availableHostRam; }
    size_t getTotalPagefile() const { return m_totalPagefile; }
    size_t getAvailablePagefile() const { return m_availablePagefile; }

    bool writeBlock(size_t blockId, const void* data, size_t size);
    bool readBlock(size_t blockId, void* outData, size_t size);
    bool write(size_t logicalOffset, const void* data, size_t size);
    bool read(size_t logicalOffset, void* outData, size_t size);
    bool getBlockLocation(size_t blockId, BlockLocation& outLocation) const;
    void dumpLayout() const;

    void registerAccess(size_t blockId);
    void logResidencyChange(size_t blockId, MemoryTier newTier, int newDeviceId);
    bool isThrashingDetected();
    bool isThrashingDetected(size_t blockId);
    bool isThrashingDetected(int windowSize, double threshold);
    bool evictLRUBlockFromGPU(int targetDeviceId, size_t requiredSpace);
    bool migrateBlockToTier(size_t blockId, MemoryTier targetTier, int targetDeviceId);

    std::pair<size_t, std::vector<size_t>> estimateWorkingSet(int windowSize);
    void prefetchBlocks(const std::vector<size_t>& blocks, MemoryTier targetTier);
    void simulatePageFault(size_t faultAddress);
    std::vector<ResidencyHistoryEntry> getResidencyHistory(size_t blockId);

    // Declarações (sem definição inline para evitar redefinição no .cpp)
    cudaStream_t getOrCreateStream(int deviceId);
    bool writeBlockAsync(size_t blockId, const void* data, size_t size, cudaStream_t stream);
    void synchronizeStream(int deviceId, cudaStream_t stream);
    void synchronizeAllStreams();

private:
    bool allocateBlockPhysical(size_t blockId, BlockLocation& outLocation);
    bool allocateVram(size_t blockId, BlockLocation& outLocation);
    bool allocateRam(size_t blockId, BlockLocation& outLocation);
    bool allocatePagefile(size_t blockId, BlockLocation& outLocation);
    bool freeBlockPhysical(size_t blockId);
    void cleanup();
    size_t getAvailableVram(int deviceId) const;

    // Membros privados
    size_t m_blockSizeBytes;
    std::unordered_map<size_t, BlockLocation> m_blocks;  // blockId -> location (renomeado de m_blockMap no patched.cpp)
    std::vector<GpuDeviceInfo> m_gpuDevices;
    std::vector<size_t> m_gpuAllocatedBytes;
    size_t m_totalVirtualCapacity;
    size_t m_totalHostRam;
    size_t m_availableHostRam;
    size_t m_totalPagefile;
    size_t m_availablePagefile;
    HANDLE m_hPagefileMapping;
    size_t m_pagefileAllocatedBytes;
    size_t m_pagefileLimitBytes;
    bool m_nvmlInitialized;
    size_t m_hostAllocatedBytes;
    size_t m_ramLimitBytes;
    size_t m_maxVramBudgetOverride;
    size_t m_maxRamBudgetOverride;
    size_t m_maxPagefileBudgetOverride;

    // Novos membros para sincronização e histórico (do patched.cpp)
    std::recursive_mutex m_mutex;  // Mutex principal para operações de memória
    std::mutex m_streamMutex;      // Mutex para streams CUDA
    std::unordered_map<int, cudaStream_t> m_streams;  // deviceId -> stream
    size_t m_globalAccessCounter;
    int m_historyWindowSize;
    int m_migrationHistoryWindowSize;

    // Histórico de acesso e thrashing detection (do patched.cpp)
    std::unordered_map<size_t, size_t> m_accessTimestamps;  // blockId -> last access time
    std::deque<ResidencyHistoryEntry> m_migrationHistory;   // histórico de migrações
};
