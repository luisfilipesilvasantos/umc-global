# UMC nativo — reescrita C++/CUDA (Fases 0–6)

Projecto nativo do UMC **dentro** de `UMC_v3_strata` (decisão de 2026-10-08:
tudo nesta árvore). Fonte canónica copiada de `C:\Users\luisf\UMC-avanca`
(originales intactos na origem); variantes em `archive/` (ver o lá README).

> **Estado**: Fase 0 concluída (diagnóstico de build + baseline medida).
> O `.dll` em produção (`ComfyUI\umc\build\`) **não é substituído** por nada
> daqui sem decisão explícita — a cadeia `ARRANQUE_UMC_v2_FIX.bat` tem de
> continuar a funcionar.

## Estrutura

```
native\
  CMakeLists.txt     build canónico (só mudou: fontes em src/)
  src\                6 fontes + 2 headers + umc.def (o que o build referencia)
  archive\            21 variantes + mocks preservados (nunca no build)
  tests\              bench_baseline.py (baseline medida) + testes futuros
  build\              artefactos CMake (recriáveis, não versionar)
  bin_v2\             exes gerados (RUNTIME_OUTPUT_DIRECTORY)
```

## Toolchain exacta (medida em 2026-10-08 nesta máquina)

| Componente | Versão | Onde |
|---|---|---|
| CMake | 4.4.2 | PATH |
| Generator | Visual Studio 18 2026 (`-A x64`) | VS Community 2026 (v18) |
| MSVC | 19.51.36256.0 (toolset 14.51.36231) | `C:\Program Files\Microsoft Visual Studio\18\Community` |
| Windows SDK | 10.0.28000.0 | — |
| CUDA (CUDAToolkit) | 13.3.73 — detectado via **miniconda** (`C:\ProgramData\miniconda3\Library`) | `where nvcc` dá miniconda 1º, `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3` 2º |
| nvcc | 13.3 | idem |
| ninja | presente (opcional) | winget + python_embeded |

Nota: a raiz do projecto tinha `cuda_runtime.h` + `host_config.h` cópias do
toolkit — verificado **byte-idênticas** ao toolkit 13.3 (md5
`2B907791873A58CF1656A16DF5079637`), por isso não foram copiadas; o
`#include <cuda_runtime.h>` resolve para o toolkit real.

## Build reproduzível (Fase 0, exit 0 verificado)

```bat
cmake -S native -B native\build -G "Visual Studio 18 2026" -A x64
cmake --build native\build --config Release
```

Resultados medidos (2026-10-08): configure 7,3 s / exit 0; build exit 0,
**6/6 alvos**, 0 erros, 14 avisos (13× C4273 ligação dll em `umc_api.h` vs
`umc_client.cpp`; 1× LNK4070 — o `LIBRARY` no `.def` ainda diz `UMC.dll`).
Alvos: `umc_v2.dll` (75 776 B), `allocator_v2.dll` (14 848 B),
`umc_hook_v2.dll`, `ollama_hook_v2.dll` (20 480 B cada),
`umc_loader_v2.exe` (35 840 B), `umc_demo_v2.exe` (13 824 B).

Pormenor: os `.dll` ficam em `native\build\Release\` (o CMakeLists define
`LIBRARY_OUTPUT_DIRECTORY`, que o MSVC ignora para DLLs — em Windows a variável
certa é `RUNTIME_OUTPUT_DIRECTORY`); os `.exe` em `native\bin_v2\Release\`.
Corrigir quando a Fase 1 redesenhar o build.

## Baseline medida (tests/bench_baseline.py)

```bat
python_embeded\python.exe native\tests\bench_baseline.py --saturation
python_embeded\python.exe native\tests\bench_baseline.py --workflow   :: requer ComfyUI a correr
```

Números em `tests/baseline_result.json` e no `DIARIO.md`. Sem mocks: o
`--saturation` corre num processo standalone **sem UMC** (baseline bruta) e o
`--workflow` usa a cadeia real `ARRANQUE_UMC_v2_FIX.bat`.
