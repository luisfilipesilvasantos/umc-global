# Diário de Progresso — UMC (Unified Memory Controller)

Este ficheiro é o histórico de decisões e progresso do projeto.
Lê sempre a entrada mais recente antes de continuares o trabalho.

---

## Decisões tomadas e porquê

- **Confirmado com evidência bruta (conteúdo de VirtualGpuMemory.h e cuda_runtime_mock.h, mais findstr com números de linha): MemoryTier, BlockLocation e GpuDeviceInfo estão definidos apenas em VirtualGpuMemory.h. cuda_runtime_mock.h já não os redefine. Não há conflito de redefinição entre estes dois ficheiros.**
  - *Porquê*: A verificação direta do conteúdo dos headers (com grep/findstr e números de linha) mostrou que as definições únicas de `enum class MemoryTier`, `struct BlockLocation` e `struct GpuDeviceInfo` ocorrem exclusivamente em VirtualGpuMemory.h. O cuda_runtime_mock.h contém apenas stubs para CUDA (cudaStream_t, cudaError_t, etc.) sem redefinir estes tipos críticos.

- **VirtualGpuMemory-Nemotron-Fixed.cpp era só referência, não para integrar. Arquivado em /archive/reference-only/. O trabalho continua exclusivamente em VirtualGpuMemory.cpp.**
  - *Porquê*: Este ficheiro foi fornecido apenas como inspiração/exemplo de implementação, nunca como candidato a fusão no projeto. Para evitar confusões futuras e manter o histórico limpo, foi movido para /archive/reference-only/.

- **Verificação da redefinição de tipos (MemoryTier, BlockLocation, GpuDeviceInfo): não há conflito entre VirtualGpuMemory.h e cuda_runtime_mock.h.**
  - *Porquê*: Após grep em todos os headers do projeto (`*.h`), confirmou-se que `enum class MemoryTier`, `struct BlockLocation` e `struct GpuDeviceInfo` só são definidos em VirtualGpuMemory.h (e backups). O cuda_runtime_mock.h apenas define tipos CUDA stubs (cudaStream_t, cudaError_t) e funções mockadas. Portanto, não há conflito real a resolver entre estes dois headers canónicos.

- **Revisão de código útil no ficheiro VirtualGpuMemory-Nemotron.cpp: não há funcionalidades adicionais para integrar.**
  - *Porquê*: O ficheiro Nemotron tem apenas 194 linhas, é uma versão incompleta/resumida do VirtualGpuMemory.cpp (que tem milhares de linhas). A implementação canónica já cobre todas as funcionalidades principais: inicialização NVML/ram/pagefile, alocação/migração/eviction de blocos, deteção de thrashing, histórico de residência e prefetching. Não há código útil adicional para extrair.

- **Fase 0 — Verificação dos ficheiros backup/alternativos (2025-04-05):**
  - *VirtualGpuMemory_backup.h*: Contém as mesmas definições de MemoryTier, BlockLocation e outras estruturas que VirtualGpuMemory.h. Não há conflito se não for incluído juntamente com o header principal.
  - *virtualgpumemory_header.cpp*: É na verdade um header (apesar do nome .cpp) com as mesmas definições de tipos e declaração de funções adicionais (writeBlockAsync, synchronizeStream). Não contém implementações — apenas declarações. Se incluído juntamente com VirtualGpuMemory.h, causaria conflito; por isso deve ser arquivado.
  - *virtualgpumemory_implementatio.cpp* e *virtualgpumemory_implementation.cpp*: Implementações parciais que incluem VirtualGpuMemory.h (sem redefinir tipos). Contêm as mesmas funções básicas do VirtualGpuMemory.cpp, mas são incompletas ou desatualizadas. Arquivar.
  - *virtualgpumemory_implementation_patched.cpp*: Implementação com funções adicionais não presentes no VirtualGpuMemory.cpp atual: writeBlockAsync (cópia assíncrona Host-to-Device via cudaStream) e synchronizeStream (sincronização de stream CUDA). Estas funções podem ser integradas ao código principal. **Decisão**: Adicionar declarações no header VirtualGpuMemory.h e implementações no VirtualGpuMemory.cpp antes de arquivar este ficheiro.

- **Fase 0 — Arquivação dos ficheiros redundantes (2025-04-05):**
  - *VirtualGpuMemory_backup.h*, *virtualgpumemory_header.cpp*, *virtualgpumemory_implementatio.cpp* e *virtualgpumemory_implementation.cpp*: Todos arquivados em /archive/backup-old/. Não trazem funcionalidades novas ou correcções críticas.
  - *virtualgpumemory_implementation_patched.cpp*: Funções writeBlockAsync e synchronizeStream foram integradas ao VirtualGpuMemory.h e VirtualGpuMemory.cpp. O ficheiro foi arquivado em /archive/backup-old/.

- **[2025-04-05] Investigação de erro de compilação no VirtualGpuMemory.cpp (linha 862): identificação de problema de chaves faltando na função getAvailableVram.**
  - *Porquê*: Ao tentar compilar, ocorreu um erro relacionado à linha 862 do VirtualGpuMemory.cpp. A leitura direta das linhas ao redor da linha 862 revelou que a implementação de `getAvailableVram` está incompleta ou com chaves desbalanceadas.

- **[2026-10-08] Fase 3 do lote Python (UMC_v3_strata) — concluída e entregue.**
  - *O que*: `umc_heat.py` (calor online por modelo — decay 0.7/epoch, histerese 1.5 —, perfil persistido com escrita atómica, sha256 interno e fallback `.bak`); `umc_integrity.py` (canário blake2b verificado a cada prompt, self-heal por `unload_all_models` com máx. 3/sessão, `roundtrip_test()` de aceitação); `umc_placement.py` (epoch + canário por prompt independente do prefetch, prefetch ordenado por calor, protecção do lote recém-carregado, evicção guardada dos modelos frios sob pressão virtual < 4 GiB, cap 3/chamada, cooldown 30 s); `umc_bridge_v3.py` (`heat_bytes.all.current/.peak` só-leitura); `start_umc_v3.py` (VRAM física livre no arranque, aviso LOW < 512 MiB); README com camadas de integridade e kill-switches.
  - *Medido (aceitação)*: ruff `--select F,E9` limpo nos 5 ficheiros; smoke com stubs **27/27** (calor + `.bak` + 8 cenários de evicção + tique); `--install-test` **INSTALL-TEST OK, exit 0** (fonte e exe); `roundtrip_test()` real na GPU **3/3 ciclos digests iguais, exit 0**.
  - *Entrega*: commit `4deeaa0` push a `github.com:luisfilipesilvasantos/umc-global` (main) via deploy key; `dist\UMCv3.exe` reconstruído e verificado.
  - *Desvio*: pressão física sozinha só gera aviso LOW — não eviciona; evicionar por exaustão física lutaria contra o paging nativo do UMC. A evicção usa a pressão **virtual** (é a visão que o ComfyUI vê).
  - *Limitação honesta*: o transporte de blocos vive nos `.dll` pré-compilados sem fonte — checksums *dentro* da transferência são impossíveis; integridade medida de fora (canário + réplica safetensors + self-heal). Protocolo com checksums por bloco = Fase 5 (reescrita nativa).
  - *Próximo*: Fase 0 da reescrita nativa neste projecto (diagnóstico de build, snapshot git, arquivamento de variantes, `tests/bench_baseline.py`).

- **[2026-10-08] Fase 0 da reescrita nativa (`UMC_v3_strata/native`) — concluída e entregue.**
  - *O que (estrutura)*: fontes canónicas movidas para `native/src/` (6 `.cpp` + `umc_api.h` + `VirtualGpuMemory.h` + `umc.def`, sem mocks — usa o `cuda_runtime.h`/`nvml.h` reais, byte-idênticos ao toolkit); 21 variantes + mocks + `CMakeLists_v2.txt` + `allocator.cpp` copiados para `native/archive/` com `README.md` (originais intactos); `CMakeLists.txt` aponta a `src/` (única mudança vs canónico); `tests/`, `README.md` criados. `UMC-avanca` permanece só-leitura.
  - *Medido (build)*: configure exit 0 em 7,3 s (VS 18 2026 `-A x64`, MSVC 19.51.36256.0, SDK 10.0.28000.0, CUDAToolkit 13.3.73); rebuild limpo (`--clean-first`) exit 0, **6/6 alvos, 14 warnings (13× C4273 + 1× LNK4070), 0 erros** — o erro histórico da linha 862 do `VirtualGpuMemory.cpp` **não se reproduz**. Artefactos: `umc_v2.dll` 75776 B, `allocator_v2.dll` 14848 B, `umc_hook_v2.dll` 20480 B, `ollama_hook_v2.dll` 20480 B, `umc_loader_v2.exe` 35840 B, `umc_demo_v2.exe` 13824 B (DLLs de produção intocadas).
  - *Medido (baseline)*: `bench_baseline.py --saturation --reuse <pid>` exit 0. Saturação standalone sem UMC: **64×256 MiB = 16,0 GiB alocados em 2,1 s** (paragem no tecto de segurança de 16 GiB; física estabilizou em ~972 MiB livres com o WDDM a paginar), a visão CUDA chega a **0 MiB livres mas as alocações continuam a ter sucesso** (oversubscription do WDDM), divergência máx. CUDA-vs-física **1869 MiB** (bloco 12; varia entre corridas: 1004/3168/1869 — depende da memória residente), libertação total **528 ms**, física depois 9932 MiB. Workflow real (prompt `36da88ea`, 7 nós, realisticVisionV51, seed 123456, cadeia UMC v2 de produção): **status=success, 1 output, exec 44,82 s** — lido do `/history` via `--reuse` (sem re-submeter render).
  - *Conflito registado*: um render WAN estrangeiro (94 nós, prompt `fc05436d`) entrou na fila da instância ComfyUI lançada para a baseline e correu primeiro (exec 01:02:49); a fila esvaziou-se sozinha (0/0) antes da medição final — nenhuma medição foi feita com GPU ocupada.
  - *Ferramenta*: `native/tests/bench_baseline.py` com `--saturation`, `--workflow`, `--reuse <prompt_id>` (ruff F,E9 limpo); `tests/baseline_result.json` regenerado completo (saturação + workflow). `ComfyUI/system_stats` no processo com UMC vê `vram_free` **51,5 GB** (pool virtual de 48) vs 12 GB físicos — prova visual do "mentir" em processo.
  - *Quirk registado (não corrigido)*: MSVC ignora `LIBRARY_OUTPUT_DIRECTORY` para DLLs → DLLs em `native/build/Release/`, exes em `native/bin_v2/Release/`; correção adiada para a Fase 1.
  - *Entrega*: commit com `native/` (35 ficheiros, excl. `build/`+`bin_v2/`), `DIARIO.md` e baseline; push a `github.com:luisfilipesilvasantos/umc-global` (main) via deploy key.
  - *Nota*: o `DIARIO.md` mestre em `UMC-avanca` NÃO é alterado (só-leitura); a entrada vive em `UMC_v3_strata/DIARIO.md`.
  - *Próximo*: Fase 1 — reescrita de `src/allocator/` (OOM-retry ladder, soft-OOM por NVML, exports duplicados, testes com exit code real).

---