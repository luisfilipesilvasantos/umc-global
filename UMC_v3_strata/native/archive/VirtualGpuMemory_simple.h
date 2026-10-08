#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include <map>

class VirtualGpuMemory {
public:
    enum class MemoryTier { Vram, Ram, Pagefile };
    
    struct BlockLocation {
        uint64_t offset;
        size_t size;
        MemoryTier tier;
    };

    bool initialize();
    void* allocate(size_t size);
    void free(void* ptr);
    bool migrateToTier(void* ptr, MemoryTier tier);
    std::string getStats() const;

private:
    uint64_t m_totalVram = 0;
    uint64_t m_totalRam = 0;
    uint64_t m_totalPagefile = 0;
    size_t m_currentUsage = 0;
    std::map<void*, BlockLocation> m_blocks;
};