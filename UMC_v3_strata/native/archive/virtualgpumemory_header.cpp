#ifndef VIRTUAL_GPU_MEMORY_H
#define VIRTUAL_GPU_MEMORY_H

#include <vector>
#include <unordered_map>
#include <deque>
#include <mutex>
#include <string>
#include <iostream>
#include <windows.h>
#include <cuda_runtime.h>
#include <nvml.h>

// Definição dos níveis de armazenamento físico de dados (Memory Tiers)
enum class MemoryTier {
    Vram,
    Ram,
    Pagefile
};

// Estrutura que rastreia a localização física exata de um bloco de memória virtualizado
struct BlockLocation {
    MemoryTier tier;
    int deviceId;             // ID da GPU se tier == Vram, caso contrário -1
    size_t physicalOffset;    // Endereço de memória física real (cast para void*)
    size_t size;              // Tamanho do bloco em bytes
    bool isPinned;            // true se alocado como cudaHostAlloc (Aperture Segment)
    size_t lastAccessTime;    // Timestamp do último acesso (para o LRU)
    size_t residentSince;     // Timestamp de quando entrou nesta tier
};

// Histórico de residência para análise preventiva de Thrashing (Journaling)
struct ResidencyHistoryEntry {
    MemoryTier tier;
    int deviceId;
    size_t timestamp;
};

class VirtualGpuMemory {
public:
    VirtualGpuMemory(size_t blockSizeBytes, size_t ramLimitMb, size_t pagefileLimitMb);
    ~VirtualGpuMemory();

    bool initialize();
    
    // API Principal de Leitura/Escrita de Dados (Síncrona e Assíncrona para RAID)
    bool write(size_t logicalOffset, const void* data, size_t size);
    bool read(size_t logicalOffset, void* outData, size_t size);

    bool writeBlock(size_t blockId, const void* data, size_t size);
    bool readBlock(size_t blockId, void* outData, size_t size);
    
    // Canal Assíncrono de Escrita de Alta Performance (PCIe Direct Transfer)
    bool writeBlockAsync(size_t blockId, const void* data, size_t size, cudaStream_t stream);
    void synchronizeStream(int deviceId, cudaStream_t stream);

    // Métodos Auxiliares de Diagnóstico e Gestão de Estado (Marcados consistentemente como const)
    bool isThrashingDetected() const; 
    bool getBlockLocation(size_t blockId, BlockLocation& outLocation) const;
    std::string tierToString(MemoryTier tier) const;

private:
    // Alocadores de Baixo Nível do Kernel UMC
    bool allocateBlockPhysical(size_t blockId, BlockLocation& outLocation);
    bool allocateVram(size_t blockId, BlockLocation& outLocation);
    bool allocateRam(size_t blockId, BlockLocation& outLocation);
    bool allocatePagefile(size_t blockId, BlockLocation& outLocation);

    // Gestão Dinâmica de Memória (Algoritmos LRU e Migração)
    bool evictLRUBlockFromGPU(int deviceId, size_t requiredSpace);
    bool migrateBlockToTier(size_t blockId, MemoryTier targetTier, int targetDeviceId);
    void registerAccess(size_t blockId);
    void logResidencyChange(size_t blockId, MemoryTier newTier, int newDeviceId);

    // Membros de Controlo de Dispositivos e Limites
    size_t m_blockSizeBytes;
    std::vector<int> m_gpuDevices;
    std::vector<size_t> m_gpuLimitBytes;
    std::vector<size_t> m_gpuAllocatedBytes;

    size_t m_ramLimitBytes;
    size_t m_hostAllocatedBytes;

    size_t m_pagefileLimitBytes;
    size_t m_pagefileAllocatedBytes;
    HANDLE m_hPagefileMapping;

    // Tabelas Hash de Mapeamento de Memória Virtual
    std::unordered_map<size_t, BlockLocation> m_blockMap;

    // Concorrência e Multi-Threading Guardrails (Fencing/WDDM-style Protection)
    mutable std::mutex m_mutex;
    std::mutex m_streamMutex;
    std::vector<cudaStream_t> m_streams;

    // Métricas para Algoritmo LRU e Detecção de Thrashing
    size_t m_globalAccessCounter;
    std::unordered_map<size_t, size_t> m_accessTimestamps;
    std::unordered_map<size_t, std::deque<ResidencyHistoryEntry>> m_residencyHistory;
    std::deque<ResidencyHistoryEntry> m_migrationHistory;

    const size_t m_historyWindowSize = 5;
    const size_t m_migrationHistoryWindowSize = 20;
    bool m_nvmlInitialized;
};

#endif // VIRTUAL_GPU_MEMORY_H