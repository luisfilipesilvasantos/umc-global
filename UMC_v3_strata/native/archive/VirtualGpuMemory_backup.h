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

struct ResidencyHistoryEntry {
    MemoryTier tier;
    int deviceId;
    uint64_t timestamp;
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
    bool isThrashingDetected(size_t blockId);
    bool isThrashingDetected(int windowSize, double threshold);
    bool evictLRUBlockFromGPU(int targetDeviceId);
    bool migrateBlockToTier(size_t blockId, MemoryTier targetTier, int targetDeviceId);

    std::pair<size_t, std::vector<size_t>> estimateWorkingSet(int windowSize);
    void prefetchBlocks(const std::vector<size_t>& blocks, MemoryTier targetTier);
    void simulatePageFault(size_t faultAddress);
    std::vector<ResidencyHistoryEntry> getResidencyHistory(size_t blockId);

    // Declarações (sem definição inline para evitar redefinição no .cpp)
    cudaStream_t getOrCreateStream(int deviceId);
    bool writeBlockAsync(size_t blockId, const void* data, size_t size, cudaStream_t stream);
    void synchronizeStream(cudaStream_t stream);
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
    size_t m_totalVirtualCapacity;
    std::vector<GpuDeviceInfo> m_gpuDevices;
    std::vector<size_t> m_gpuAllocatedBytes;
    std::vector<size_t> m_gpuLimitBytes;
    size_t m_totalHostRam;
    size_t m_availableHostRam;
    size_t m_totalPagefile;
    size_t m_availablePagefile;

    HANDLE m_hPagefileMapping;
    size_t m_pagefileAllocatedBytes;
    size_t m_pagefileLimitBytes;
    bool m_nvmlInitialized;
    uint64_t m_globalAccessCounter;
    size_t m_historyWindowSize;
    size_t m_migrationHistoryWindowSize;
    size_t m_hostAllocationThreshold;

    // Membros de sincronização e mapas
    std::recursive_mutex m_mutex;
    std::unordered_map<size_t, BlockLocation> m_blockMap;
    std::unordered_map<size_t, std::vector<char>> m_blockData;
    size_t m_hostAllocatedBytes;
    size_t m_ramLimitBytes;
    size_t m_maxVramBudgetOverride;
    size_t m_maxRamBudgetOverride;
    size_t m_maxPagefileBudgetOverride;

    // Membros para alocações de sistema (usados no .cpp)
    std::unordered_map<void*, AllocationEntry> m_systemAllocations;
    std::unordered_map<void*, SystemAllocationEntry> m_systemAllocationsTrack;

    // Membros CUDA — sempre presentes, mas vazios quando sem CUDA
    std::unordered_map<int, cudaStream_t> m_streams;
    std::mutex m_streamMutex;

    // Membros de histórico
    std::deque<std::pair<uint64_t, size_t>> m_accessHistory;
    std::vector<ResidencyHistoryEntry> m_residencyHistory;
};

