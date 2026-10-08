#include "VirtualGpuMemory.h"
#include <algorithm>
#include <iomanip>
#include <cstring>

VirtualGpuMemory::VirtualGpuMemory(size_t blockSizeBytes, size_t ramLimitMb, size_t pagefileLimitMb)
    : m_blockSizeBytes(blockSizeBytes),
      m_hostAllocatedBytes(0),
      m_pagefileAllocatedBytes(0),
      m_hPagefileMapping(nullptr),
      m_globalAccessCounter(0),
      m_nvmlInitialized(false) {
    
    m_ramLimitBytes = ramLimitMb * 1024 * 1024;
    m_pagefileLimitBytes = pagefileLimitMb * 1024 * 1024;
}

VirtualGpuMemory::~VirtualGpuMemory() {
    std::lock_guard<std::mutex> lock(m_mutex);
    
    // Liberta todos os blocos de memória virtualizada alocados pelo UMC
    for (const auto& [blockId, loc] : m_blockMap) {
        if (loc.tier == MemoryTier::Vram) {
            cudaSetDevice(loc.deviceId);
            cudaFree(reinterpret_cast<void*>(loc.physicalOffset));
        } else if (loc.tier == MemoryTier::Ram) {
            if (loc.isPinned) {
                cudaHostFree(reinterpret_cast<void*>(loc.physicalOffset));
            } else {
                VirtualFree(reinterpret_cast<void*>(loc.physicalOffset), 0, MEM_RELEASE);
            }
        }
    }
    m_blockMap.clear();

    // Fecha Handles do ficheiro de paginação (Windows Pagefile)
    if (m_hPagefileMapping) {
        CloseHandle(m_hPagefileMapping);
        m_hPagefileMapping = nullptr;
    }

    // Desativa os streams CUDA assíncronos usados na fila de paginação (RAID pipeline)
    std::lock_guard<std::mutex> streamLock(m_streamMutex);
    for (size_t i = 0; i < m_streams.size(); ++i) {
        cudaSetDevice(static_cast<int>(i));
        cudaStreamSynchronize(m_streams[i]);
        cudaStreamDestroy(m_streams[i]);
    }
    m_streams.clear();

    if (m_nvmlInitialized) {
        nvmlShutdown();
        m_nvmlInitialized = false;
    }
}

bool VirtualGpuMemory::initialize() {
    std::lock_guard<std::mutex> lock(m_mutex);
    
    // Inicialização da API NVML para telemetria em tempo real
    if (nvmlInit() == NVML_SUCCESS) {
        m_nvmlInitialized = true;
    }

    int deviceCount = 0;
    cudaError_t err = cudaGetDeviceCount(&deviceCount);
    if (err != cudaSuccess || deviceCount == 0) {
        std::cerr << "[UMC ERROR] Nenhuma GPU CUDA disponível para striping RAID.\n";
        return false;
    }

    // Mapeamento dinâmico das GPUs instaladas no Windows 11
    m_streams.resize(deviceCount);
    for (int i = 0; i < deviceCount; ++i) {
        m_gpuDevices.push_back(i);
        
        cudaDeviceProp prop;
        cudaGetDeviceProperties(&prop, i);
        
        // Define o limite de alocação segura em 85% para evitar colisões com o Windows DWM
        size_t safeLimit = static_cast<size_t>(prop.totalGlobalMem * 0.85);
        m_gpuLimitBytes.push_back(safeLimit);
        m_gpuAllocatedBytes.push_back(0);

        // Cria canais PCIe assíncronos dedicados a cada placa gráfica (WDDM-Style)
        cudaSetDevice(i);
        cudaStreamCreate(&m_streams[i]);
        
        std::cout << "[UMC INIT] GPU " << i << ": " << prop.name 
                  << " | Limite Seguro RAID: " << (safeLimit / (1024 * 1024)) << " MB\n";
    }

    return true;
}

bool VirtualGpuMemory::allocateBlockPhysical(size_t blockId, BlockLocation& outLocation) {
    if (isThrashingDetected()) {
        std::cout << "[UMC POLICY] Thrashing detetado! Bypass temporário das VRAMs direto para RAM Pinned.\n";
        if (allocateRam(blockId, outLocation)) { m_blockMap[blockId] = outLocation; return true; }
        if (allocatePagefile(blockId, outLocation)) { m_blockMap[blockId] = outLocation; return true; }
    }

    // Tenta alocar na VRAM usando a distribuição Stride (RAID 0)
    if (allocateVram(blockId, outLocation)) { m_blockMap[blockId] = outLocation; return true; }
    if (allocateRam(blockId, outLocation)) { m_blockMap[blockId] = outLocation; return true; }
    if (allocatePagefile(blockId, outLocation)) { m_blockMap[blockId] = outLocation; return true; }
    return false;
}

bool VirtualGpuMemory::allocateVram(size_t blockId, BlockLocation& outLocation) {
    // Cálculo de Stride Circular baseada no ID do bloco (Memory Striping real)
    size_t targetGpu = blockId % m_gpuDevices.size();

    for (size_t idx = 0; idx < m_gpuDevices.size(); ++idx) {
        size_t i = (targetGpu + idx) % m_gpuDevices.size();
        size_t gpuLimit = m_gpuLimitBytes[i];
        
        if (m_gpuAllocatedBytes[i] + m_blockSizeBytes <= gpuLimit) {
            cudaError_t err = cudaSetDevice(static_cast<int>(i));
            if (err != cudaSuccess) continue;

            void* devPtr = nullptr;
            err = cudaMalloc(&devPtr, m_blockSizeBytes);
            if (err == cudaSuccess) {
                cudaMemset(devPtr, 0, m_blockSizeBytes);
                
                outLocation.tier = MemoryTier::Vram;
                outLocation.deviceId = static_cast<int>(i);
                outLocation.physicalOffset = reinterpret_cast<size_t>(devPtr);
                outLocation.size = m_blockSizeBytes;
                outLocation.isPinned = false;
                outLocation.lastAccessTime = m_globalAccessCounter;
                outLocation.residentSince = m_globalAccessCounter;
                
                m_gpuAllocatedBytes[i] += m_blockSizeBytes;
                std::cout << "[UMC RAID-0] Bloco " << blockId << " -> GPU " << i 
                          << " (VRAM Striped) at 0x" << std::hex << outLocation.physicalOffset << std::dec << "\n";
                return true;
            } else {
                // Se a GPU estiver sem espaço, dispara a desalocação LRU preventiva do UMC
                if (cudaGetLastError() == cudaErrorMemoryAllocation) {
                    std::cout << "[UMC EVICT] VRAM cheia na GPU " << i << ". Iniciando despejo LRU...\n";
                    if (evictLRUBlockFromGPU(static_cast<int>(i), m_blockSizeBytes)) {
                        err = cudaMalloc(&devPtr, m_blockSizeBytes);
                        if (err == cudaSuccess) {
                            cudaMemset(devPtr, 0, m_blockSizeBytes);
                            outLocation.tier = MemoryTier::Vram;
                            outLocation.deviceId = static_cast<int>(i);
                            outLocation.physicalOffset = reinterpret_cast<size_t>(devPtr);
                            outLocation.size = m_blockSizeBytes;
                            outLocation.isPinned = false;
                            outLocation.lastAccessTime = m_globalAccessCounter;
                            outLocation.residentSince = m_globalAccessCounter;
                            m_gpuAllocatedBytes[i] += m_blockSizeBytes;
                            return true;
                        }
                    }
                }
                cudaGetLastError(); // Limpa estado de erro interno da CUDA
            }
        }
    }
    return false;
}

bool VirtualGpuMemory::allocateRam(size_t blockId, BlockLocation& outLocation) {
    if (m_hostAllocatedBytes + m_blockSizeBytes <= m_ramLimitBytes) {
        void* ramPtr = nullptr;
        // Alocação em modo Pinned (não-paginável) para máxima velocidade via barramento PCIe (Aperture Segment)
        cudaError_t err = cudaHostAlloc(&ramPtr, m_blockSizeBytes, cudaHostAllocMapped);
        
        if (err == cudaSuccess && ramPtr) {
            outLocation.tier = MemoryTier::Ram;
            outLocation.deviceId = -1;
            outLocation.physicalOffset = reinterpret_cast<size_t>(ramPtr);
            outLocation.size = m_blockSizeBytes;
            outLocation.isPinned = true;
            outLocation.lastAccessTime = m_globalAccessCounter;
            outLocation.residentSince = m_globalAccessCounter;
            
            m_hostAllocatedBytes += m_blockSizeBytes;
            std::cout << "[UMC ALLOC] Bloco " << blockId << " -> System RAM (Aperture Segment PCIe) at 0x" 
                      << std::hex << outLocation.physicalOffset << std::dec << "\n";
            return true;
        } else {
            // Fallback clássico se a CUDA Host Alloc falhar por falta de recursos do driver
            void* stdPtr = VirtualAlloc(nullptr, m_blockSizeBytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
            if (stdPtr) {
                outLocation.tier = MemoryTier::Ram;
                outLocation.deviceId = -1;
                outLocation.physicalOffset = reinterpret_cast<size_t>(stdPtr);
                outLocation.size = m_blockSizeBytes;
                outLocation.isPinned = false;
                outLocation.lastAccessTime = m_globalAccessCounter;
                outLocation.residentSince = m_globalAccessCounter;
                m_hostAllocatedBytes += m_blockSizeBytes;
                return true;
            }
        }
    }
    return false;
}

bool VirtualGpuMemory::allocatePagefile(size_t blockId, BlockLocation& outLocation) {
    if (m_pagefileAllocatedBytes + m_blockSizeBytes <= m_pagefileLimitBytes) {
        if (!m_hPagefileMapping) {
            m_hPagefileMapping = CreateFileMappingW(
                INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE,
                static_cast<DWORD>(m_pagefileLimitBytes >> 32),
                static_cast<DWORD>(m_pagefileLimitBytes & 0xFFFFFFFF),
                L"Local\\UMCPagefileStore"
            );
        }
        if (m_hPagefileMapping) {
            void* pBuf = MapViewOfFile(
                m_hPagefileMapping, FILE_MAP_ALL_ACCESS,
                static_cast<DWORD>(m_pagefileAllocatedBytes >> 32),
                static_cast<DWORD>(m_pagefileAllocatedBytes & 0xFFFFFFFF),
                m_blockSizeBytes
            );
            if (pBuf) {
                outLocation.tier = MemoryTier::Pagefile;
                outLocation.deviceId = -1;
                outLocation.physicalOffset = reinterpret_cast<size_t>(pBuf);
                outLocation.size = m_blockSizeBytes;
                outLocation.isPinned = false;
                outLocation.lastAccessTime = m_globalAccessCounter;
                outLocation.residentSince = m_globalAccessCounter;
                
                m_pagefileAllocatedBytes += m_blockSizeBytes;
                std::cout << "[UMC ALLOC] Bloco " << blockId << " -> NVMe Pagefile at 0x" 
                      << std::hex << outLocation.physicalOffset << std::dec << "\n";
                return true;
            }
        }
    }
    return false;
}

bool VirtualGpuMemory::writeBlock(size_t blockId, const void* data, size_t size) {
    std::lock_guard<std::mutex> lock(m_mutex);
    registerAccess(blockId);

    BlockLocation loc;
    if (!getBlockLocation(blockId, loc)) {
        if (!allocateBlockPhysical(blockId, loc)) return false;
    }

    if (loc.tier == MemoryTier::Vram) {
        cudaSetDevice(loc.deviceId);
        cudaError_t err = cudaMemcpy(reinterpret_cast<void*>(loc.physicalOffset), data, size, cudaMemcpyHostToDevice);
        return (err == cudaSuccess);
    } else {
        std::memcpy(reinterpret_cast<void*>(loc.physicalOffset), data, size);
        return true;
    }
}

bool VirtualGpuMemory::readBlock(size_t blockId, void* outData, size_t size) {
    std::lock_guard<std::mutex> lock(m_mutex);
    registerAccess(blockId);

    BlockLocation loc;
    if (!getBlockLocation(blockId, loc)) return false;

    // Se o bloco foi paginado para fora da VRAM, migra de volta antes de ler (Re-commitment WDDM-Style)
    if (loc.tier != MemoryTier::Vram) {
        std::cout << "[UMC SWAP] Cache-miss! Puxando bloco " << blockId << " de " << tierToString(loc.tier) << " para VRAM.\n";
        if (!migrateBlockToTier(blockId, MemoryTier::Vram, blockId % m_gpuDevices.size())) {
            // Fallback de leitura direta se não for possível realocar na VRAM
            std::memcpy(outData, reinterpret_cast<void*>(loc.physicalOffset), size);
            return true;
        }
        getBlockLocation(blockId, loc);
    }

    cudaSetDevice(loc.deviceId);
    cudaError_t err = cudaMemcpy(outData, reinterpret_cast<void*>(loc.physicalOffset), size, cudaMemcpyDeviceToHost);
    return (err == cudaSuccess);
}

bool VirtualGpuMemory::writeBlockAsync(size_t blockId, const void* data, size_t size, cudaStream_t stream) {
    std::lock_guard<std::mutex> lock(m_mutex);
    registerAccess(blockId);

    BlockLocation loc;
    if (!getBlockLocation(blockId, loc)) {
        if (!allocateBlockPhysical(blockId, loc)) return false;
    }

    if (loc.tier == MemoryTier::Vram) {
        cudaSetDevice(loc.deviceId);
        // Transferência assíncrona PCIe sem bloquear a Thread de execução do ComfyUI/Ollama
        cudaError_t err = cudaMemcpyAsync(reinterpret_cast<void*>(loc.physicalOffset), data, size, cudaMemcpyHostToDevice, stream);
        return (err == cudaSuccess);
    }
    return false;
}

void VirtualGpuMemory::synchronizeStream(int deviceId, cudaStream_t stream) {
    cudaSetDevice(deviceId);
    cudaStreamSynchronize(stream);
}

bool VirtualGpuMemory::evictLRUBlockFromGPU(int deviceId, size_t requiredSpace) {
    size_t oldestBlockId = 0;
    size_t oldestTime = (size_t)-1; // Equivalente a numeric_limits max
    bool found = false;

    for (const auto& [blockId, loc] : m_blockMap) {
        if (loc.tier == MemoryTier::Vram && loc.deviceId == deviceId) {
            if (loc.lastAccessTime < oldestTime) {
                oldestTime = loc.lastAccessTime;
                oldestBlockId = blockId;
                found = true;
            }
        }
    }

    if (found) {
        std::cout << "[UMC LRU] Despejando Bloco " << oldestBlockId << " da GPU " << deviceId << " para RAM Pinned.\n";
        return migrateBlockToTier(oldestBlockId, MemoryTier::Ram, -1);
    }
    return false;
}

bool VirtualGpuMemory::migrateBlockToTier(size_t blockId, MemoryTier targetTier, int targetDeviceId) {
    auto it = m_blockMap.find(blockId);
    if (it == m_blockMap.end()) return false;

    BlockLocation sourceLoc = it->second;
    if (sourceLoc.tier == targetTier && sourceLoc.deviceId == targetDeviceId) return true;

    BlockLocation targetLoc;
    // Garanta o destino de alocação de forma segura
    if (targetTier == MemoryTier::Vram) {
        if (!allocateVram(blockId, targetLoc)) return false;
    } else if (targetTier == MemoryTier::Ram) {
        if (!allocateRam(blockId, targetLoc)) return false;
    } else {
        if (!allocatePagefile(blockId, targetLoc)) return false;
    }

    // Copia segura de dados entre barramentos usando a CUDA Runtime API
    cudaError_t err;
    if (sourceLoc.tier == MemoryTier::Vram && targetLoc.tier == MemoryTier::Vram) {
        // Transferência P2P direta via ponte NVLink ou barramento PCIe
        cudaSetDevice(sourceLoc.deviceId);
        err = cudaMemcpyPeer(
            reinterpret_cast<void*>(targetLoc.physicalOffset), targetLoc.deviceId,
            reinterpret_cast<void*>(sourceLoc.physicalOffset), sourceLoc.deviceId,
            m_blockSizeBytes
        );
    } else if (sourceLoc.tier == MemoryTier::Vram) {
        cudaSetDevice(sourceLoc.deviceId);
        err = cudaMemcpy(reinterpret_cast<void*>(targetLoc.physicalOffset), reinterpret_cast<void*>(sourceLoc.physicalOffset), m_blockSizeBytes, cudaMemcpyDeviceToHost);
    } else if (targetLoc.tier == MemoryTier::Vram) {
        cudaSetDevice(targetLoc.deviceId);
        err = cudaMemcpy(reinterpret_cast<void*>(targetLoc.physicalOffset), reinterpret_cast<void*>(sourceLoc.physicalOffset), m_blockSizeBytes, cudaMemcpyHostToDevice);
    } else {
        std::memcpy(reinterpret_cast<void*>(targetLoc.physicalOffset), reinterpret_cast<void*>(sourceLoc.physicalOffset), m_blockSizeBytes);
        err = cudaSuccess;
    }

    if (err != cudaSuccess) {
        // Caso falhe, liberta a alocação de destino incompleta e aborta para não corromper o Grafo
        if (targetLoc.tier == MemoryTier::Vram) {
            cudaSetDevice(targetLoc.deviceId);
            cudaFree(reinterpret_cast<void*>(targetLoc.physicalOffset));
            m_gpuAllocatedBytes[targetLoc.deviceId] -= m_blockSizeBytes;
        }
        return false;
    }

    // Libertação limpa e sem fugas de memória (Zero memory leaks) do bloco de origem
    if (sourceLoc.tier == MemoryTier::Vram) {
        cudaSetDevice(sourceLoc.deviceId);
        cudaFree(reinterpret_cast<void*>(sourceLoc.physicalOffset));
        m_gpuAllocatedBytes[sourceLoc.deviceId] -= m_blockSizeBytes;
    } else if (sourceLoc.tier == MemoryTier::Ram) {
        if (sourceLoc.isPinned) {
            cudaHostFree(reinterpret_cast<void*>(sourceLoc.physicalOffset));
        } else {
            VirtualFree(reinterpret_cast<void*>(sourceLoc.physicalOffset), 0, MEM_RELEASE);
        }
        m_hostAllocatedBytes -= m_blockSizeBytes;
    }

    // Atualiza tabela de residência e o histórico de paginação para diagnóstico de thrashing
    it->second = targetLoc;
    logResidencyChange(blockId, targetTier, targetDeviceId);
    return true;
}

bool VirtualGpuMemory::write(size_t logicalOffset, const void* data, size_t size) {
    size_t startBlock = logicalOffset / m_blockSizeBytes;
    size_t blockOffset = logicalOffset % m_blockSizeBytes;
    
    if (blockOffset + size <= m_blockSizeBytes) {
        // Ajusta a cópia se for menor ou igual ao tamanho do bloco
        return writeBlock(startBlock, data, size);
    }
    return false;
}

bool VirtualGpuMemory::read(size_t logicalOffset, void* outData, size_t size) {
    size_t startBlock = logicalOffset / m_blockSizeBytes;
    size_t blockOffset = logicalOffset % m_blockSizeBytes;
    
    if (blockOffset + size <= m_blockSizeBytes) {
        return readBlock(startBlock, outData, size);
    }
    return false;
}

bool VirtualGpuMemory::getBlockLocation(size_t blockId, BlockLocation& outLocation) const {
    auto it = m_blockMap.find(blockId);
    if (it != m_blockMap.end()) {
        outLocation = it->second;
        return true;
    }
    return false;
}

bool VirtualGpuMemory::isThrashingDetected() const {
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_migrationHistory.size() >= 10) {
        size_t vramToRamCount = 0;
        for (const auto& entry : m_migrationHistory) {
            if (entry.tier == MemoryTier::Ram) vramToRamCount++;
        }
        // Se 70% das migrações recentes forem despejos de VRAM para RAM, ativa a barreira de segurança de Thrashing
        return vramToRamCount > (m_migrationHistory.size() * 0.7);
    }
    return false;
}

void VirtualGpuMemory::registerAccess(size_t blockId) {
    m_accessTimestamps[blockId] = ++m_globalAccessCounter;
    auto it = m_blockMap.find(blockId);
    if (it != m_blockMap.end()) {
        it->second.lastAccessTime = m_globalAccessCounter;
    }
}

void VirtualGpuMemory::logResidencyChange(size_t blockId, MemoryTier newTier, int newDeviceId) {
    ResidencyHistoryEntry entry = {newTier, newDeviceId, m_globalAccessCounter};
    auto& history = m_residencyHistory[blockId];
    history.push_back(entry);
    if (history.size() > m_historyWindowSize) {
        history.pop_front(); 
    }
    m_migrationHistory.push_back(entry);
    if (m_migrationHistory.size() > m_migrationHistoryWindowSize) {
        m_migrationHistory.pop_front();
    }
}

std::string VirtualGpuMemory::tierToString(MemoryTier tier) const {
    switch (tier) {
        case MemoryTier::Vram: return "VRAM";
        case MemoryTier::Ram: return "System RAM";
        case MemoryTier::Pagefile: return "NVMe Pagefile";
        default: return "Unknown";
    }
}