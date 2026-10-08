cmake_minimum_required(VERSION 3.28)
project(UMC_v2 VERSION 1.0.0 LANGUAGES CXX)

# Modern CUDA detection (replaces deprecated find_package(CUDA))
find_package(CUDAToolkit REQUIRED)

# --- Core Library (umc_v2.dll) ---
# Fase 0: fontes movidas para src/ (organização Strata: src/ + tests/ + archive/);
# resto do ficheiro idêntico ao CMakeLists.txt canónico de UMC-avanca.
add_library(UMC_v2 SHARED
    src/umc_client.cpp
    src/VirtualGpuMemory.cpp
    src/umc.def
)

target_include_directories(UMC_v2 PRIVATE ${PROJECT_SOURCE_DIR})
set_target_properties(UMC_v2 PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    WINDOWS_EXPORT_ALL_SYMBOLS OFF
    POSITION_INDEPENDENT_CODE ON
    OUTPUT_NAME "umc_v2"
    LIBRARY_OUTPUT_DIRECTORY ${PROJECT_SOURCE_DIR}/bin_v2
)

target_link_directories(UMC_v2 PRIVATE ${CUDAToolkit_LIBRARY_DIR})
target_link_libraries(UMC_v2 PRIVATE
    CUDA::cudart
    nvml.lib
    psapi.lib
    kernel32.lib
    user32.lib
    gdi32.lib
    winspool.lib
    shell32.lib
    ole32.lib
    oleaut32.lib
    uuid.lib
    comdlg32.lib
    advapi32.lib
)

# --- Hook DLL (umc_hook_v2.dll) ---
add_library(umc_hook_v2 SHARED src/umc_hook.cpp)
set_target_properties(umc_hook_v2 PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    OUTPUT_NAME "umc_hook_v2"
    LIBRARY_OUTPUT_DIRECTORY ${PROJECT_SOURCE_DIR}/bin_v2
)
target_include_directories(umc_hook_v2 PRIVATE ${PROJECT_SOURCE_DIR} ${CUDAToolkit_INCLUDE_DIRS})
# Fase 1: o hook consulta a ladder e a vista virtual no allocator_v2.dll.
target_link_libraries(umc_hook_v2 PRIVATE psapi.lib CUDA::cudart allocator_v2)

# --- Loader/Injector (umc_loader_v2.exe) ---
add_executable(umc_loader_v2 src/umc_loader.cpp)
set_target_properties(umc_loader_v2 PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    OUTPUT_NAME "umc_loader_v2"
    RUNTIME_OUTPUT_DIRECTORY ${PROJECT_SOURCE_DIR}/bin_v2
)

# --- Demo App (umc_demo_v2.exe) ---
add_executable(umc_demo_v2 src/umc_demo.cpp)
set_target_properties(umc_demo_v2 PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    OUTPUT_NAME "umc_demo_v2"
    RUNTIME_OUTPUT_DIRECTORY ${PROJECT_SOURCE_DIR}/bin_v2
)
target_link_libraries(umc_demo_v2 PRIVATE UMC_v2)

# --- Allocator DLL (allocator_v2.dll) ---
# Fase 1: allocator partilhado (src/allocator/) — dono do estado (ladder,
# soft-OOM NVML, vista virtual). Exporta o contrato de producao
# (umc_alloc/umc_free) + as novas funcoes da ladder.
add_library(allocator_v2 SHARED
    src/umc_pytorch_allocator.cpp
    src/allocator/allocator.cpp
)
set_target_properties(allocator_v2 PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    OUTPUT_NAME "allocator_v2"
    LIBRARY_OUTPUT_DIRECTORY ${PROJECT_SOURCE_DIR}/bin_v2
)
target_include_directories(allocator_v2 PRIVATE ${PROJECT_SOURCE_DIR})
target_link_directories(allocator_v2 PRIVATE ${CUDAToolkit_LIBRARY_DIR})
target_link_libraries(allocator_v2 PRIVATE CUDA::cudart nvml.lib UMC_v2)

# --- Ollama Hook DLL (ollama_hook_v2.dll) ---
add_library(ollama_hook_v2 SHARED src/umc_hook.cpp)
set_target_properties(ollama_hook_v2 PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    OUTPUT_NAME "ollama_hook_v2"
    LIBRARY_OUTPUT_DIRECTORY ${PROJECT_SOURCE_DIR}/bin_v2
)
target_include_directories(ollama_hook_v2 PRIVATE ${PROJECT_SOURCE_DIR} ${CUDAToolkit_INCLUDE_DIRS})
# Fase 1: o hook consulta a ladder e a vista virtual no allocator_v2.dll.
target_link_libraries(ollama_hook_v2 PRIVATE psapi.lib CUDA::cudart allocator_v2)