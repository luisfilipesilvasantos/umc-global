# archive/ — variantes preservadas (nada foi apagado)

Ficheiros **copiados** de `C:\Users\luisf\UMC-avanca` (os originais continuam
intactos nessa árvore). Não participam no build (`CMakeLists.txt` só referencia
`src/`); estão aqui como referência histórica e para nunca serem re-introduzidos
no caminho de produção.

| Grupo | Ficheiros | Motivo do arquivamento |
|---|---|---|
| Variantes de header | `VirtualGpuMemory_backup.h`, `_final.h`, `_final_v2…v5.h`, `_fixed.h`, `_fixed2.h`, `_simple.h` | sucessivas "versões finais"; o canónico é `src/VirtualGpuMemory.h` |
| Variantes de fonte | `VirtualGpuMemory_backup.cpp`, `_simple.cpp`, `-Nemotron.cpp`, `virtualgpumemory_header.cpp`, `virtualgpumemory_implementatio.cpp`, `virtualgpumemory_implementation.cpp`, `virtualgpumemory_implementation_patched.cpp` | incompletas/desactualizadas; canónico = `src/VirtualGpuMemory.cpp` |
| Mocks CUDA/NVML | `cuda_runtime_mock.h`, `mock_cuda.h`, `nvml_mock.h` | **nunca na produção** — só os headers variantes os incluem; o canónico usa `<cuda_runtime.h>`/`<nvml.h>` reais |
| Build antigo | `CMakeLists_v2.txt` | versão anterior do build |
| Allocator anterior | `allocator.cpp` | não referenciado pelo build canónico; contém o padrão known-bad (pinned-host como ponteiro de device + `throw std::bad_alloc()`) — servirá de contraponto na Fase 1 |

Nota: a raiz de `UMC-avanca` tem ainda outros ficheiros UMC não canónicos
(`main.cpp`, `ollama_hook.cpp`, `umc_no_cuda.cpp`, `UMCModelLoader.*`,
`UMC_add_functions.cpp`, …) que **não** foram importados — permanecem na origem.
