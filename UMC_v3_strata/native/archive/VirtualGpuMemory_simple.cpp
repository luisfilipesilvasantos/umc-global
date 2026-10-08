#include "VirtualGpuMemory_simple.h"
#include <iostream>
#include <cstdlib>

bool VirtualGpuMemory::initialize() {
    m_totalVram = 8ULL * 1024 * 1024 * 1024; // 8GB VRAM simulado
    m_totalRam = 32ULL * 1024 * 1024 * 1024; // 32GB RAM simulada
    m_totalPagefile = 64ULL * 1024 * 1024 * 1024; // 64GB pagefile
    std::cout << "VirtualGpuMemory inicializado: VRAM=" << (m_totalVram>>30) << "GB, RAM=" << (m_totalRam>>30) << "GB\n";
    return true;
}

void* VirtualGpuMemory::allocate(size_t size) {
    void* ptr = std::malloc(size);
    if (ptr) {
        m_blocks[ptr] = {0, size, MemoryTier::Ram};
        m_currentUsage += size;
    }
    return ptr;
}

void VirtualGpuMemory::free(void* ptr) {
    auto it = m_blocks.find(ptr);
    if (it != m_blocks.end()) {
        m_currentUsage -= it->second.size;
        std::free(ptr);
        m_blocks.erase(it);
    }
}

bool VirtualGpuMemory::migrateToTier(void* ptr, MemoryTier tier) {
    auto it = m_blocks.find(ptr);
    if (it != m_blocks.end()) {
        it->second.tier = tier;
        return true;
    }
    return false;
}

std::string VirtualGpuMemory::getStats() const {
    char buf[256];
    snprintf(buf, sizeof(buf), "Uso atual: %zu MB / VRAM=%llu GB, RAM=%llu GB", 
             m_currentUsage >> 20, m_totalVram>>30, m_totalRam>>30);
    return std::string(buf);
}