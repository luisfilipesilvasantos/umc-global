# UMC — VRAM virtual para modelos de IA maiores que a tua GPU

> **English summary:** UMC is a Windows/NVIDIA/PyTorch layer that extends your
> GPU's VRAM into system RAM + pagefile, so models **bigger than your VRAM**
> load and run anyway. This repo ships two ready-to-use packages: a **generic
> runner** for any Python/CUDA app (Gradio/HuggingFace inference scripts) and a
> **ComfyUI integration** with synchronous predictive prefetch. Source +
> portable executables + validation results included.

**Plataforma:** Windows 10/11 · **GPU:** NVIDIA CUDA · **Camada:** PyTorch
(pluggable allocator + IAT hooks) · **Testado em:** RTX 3060 12 GB / 64 GB RAM /
ComfyUI v0.34.0

---

## O problema

Modelos de IA atuais (SDXL, Flux, LLMs, VAEs grandes…) pedem 15–70 GB de VRAM.
Uma GPU doméstica tem 8–16 GB. Sem virtualização, o carregamento falha com
`CUDA out of memory` mesmo tendo 64 GB de RAM livres à espera.

## O que é o UMC

O UMC apresenta ao torch uma **VRAM virtual ampliada** (default 48 GB)
distribuída por três tiers:

```
VRAM física (12 GB)  →  RAM do sistema (48 GB)  →  Pagefile do Windows (40 GB)
         [ rápido ]            [ médio ]               [ lento, último recurso ]
```

Três camadas técnicas (motor nativo já compilado, sem alterações):

1. **Pluggable allocator do PyTorch** (`allocator_v2.dll`) — todas as alocações
   `torch.cuda` passam por ele; decide o tier de cada tensor.
2. **IAT hooks** (`umc_hook_v2.dll`) — intercepta chamadas CUDA fora do torch
   (5 slots), para apps que usem CUDA directamente.
3. **Visão virtual de `mem_get_info`** (`VirtualGpuMemory.dll`) — o framework
   (ComfyUI, Accelerate, scripts) vê a capacidade total e admite modelos
   grandes demais para a GPU física, exactamente como faria com VRAM real.

Mais a camada Python (**bridge**) que instala tudo *antes* de o torch tocar em
CUDA, mais telemetria honesta em NVML (físico real) para diagnóstico.

## Componentes deste repositório

| Pasta | O que é | Estado |
|---|---|---|
| [`umc-global/`](umc-global/) | **Runner genérico** — arranca qualquer script Python/CUDA com UMC activo. Source + `UMCglobal.exe` portátil. | ✅ validado |
| [`UMC_v3_strata/`](UMC_v3_strata/) | **UMC v3 para ComfyUI** — contabilidade dual-track (virtual para admissão + física NVML honesta) e **prefetch síncrono** de modelos no boundary de cada nó. Source + `UMCv3.exe`. | ✅ validado |
| `ARRANQUE_UMC_V3_TEST.bat` | Launcher do ComfyUI com o v3 (copia local, aponta para o teu portable). | ✅ |
| [`tests/`](tests/) | Harnesses de validação: fila de workflows para o servidor (`queue.ps1` + `wf.json`), teste de alocação 14 GB, teste de argv. | ✅ |

Cada pasta tem o seu `README.md` com detalhes internos.

## Requisitos

- Windows 10/11 com GPU NVIDIA e drivers actuais.
- Python com **PyTorch CUDA** (testado com o `python_embeded` 3.12.10 +
  torch cu126 do portable ComfyUI; qualquer Python 3.12 com torch serve).
- Pagefile do Windows activa (o UMC usa-a como tier de último recurso).
- `pynvml` para a visão física (vem com o torch/nvidia-ml-py; opcional mas
  recomendado).

## Arranque rápido

### 1. Qualquer app Python/CUDA (`umc-global`)

```bat
:: pela fonte (sem rebuild; edições valem de imediato)
umc-global\run_umc_global.bat C:\apps\meu_gradio\app.py --port 7860

:: pelo executável (funciona de qualquer directório)
umc-global\dist\UMCglobal.exe C:\apps\meu_gradio\app.py --port 7860

:: só diagnosticar (não arranca app nenhuma)
umc-global\run_umc_global.bat --install-test
```

As dependências da tua app instalam-se no mesmo Python:
`python_embeded\python.exe -m pip install gradio ...`

### 2. ComfyUI (`UMC_v3_strata`)

```bat
ARRANQUE_UMC_V3_TEST.bat
```

Equivalente a correr `start_umc_v3.py` com `--umc-vram 48 --umc-ram 48
--umc-pagefile 40 … --comfyui-dir <ComfyUI>` e o resto das flags normais do
ComfyUI em passthrough. Para instalar/verificar sem arrancar o servidor:

```bat
python_embeded\python.exe UMC_v3_strata\src\start_umc_v3.py --comfyui-dir ComfyUI --install-test
```

Nota: `start_umc_v3.py` força sempre `--disable-cuda-malloc` mesmo quando o
argv vem sem flags (ex.: `UMCv3.exe` de duplo-clique). Sem isso o
`cuda_malloc.py` do ComfyUI (>= 0.34) activa `backend:cudaMallocAsync` depois
do bridge instalar o pluggable e o torch faz assert em runtime
(`cudaMallocAsync != pluggable`, ex.: `torch.istft`/SAM3). Para o conjunto
completo de flags recomendadas (`--reserve-vram`,
`--use-pytorch-cross-attention`, …) usa o `.bat`.

## Como funciona (arquitetura)

```
run_umc_global.bat / UMCglobal.exe / ARRANQUE_UMC_V3_TEST.bat
        │
        ▼
1. ambiente  (limpa PYTORCH_CUDA_ALLOC_CONF, DISABLE_SAGEATTENTION=1, UMC_V2=1)
        │        ← tem de ser ANTES de o torch importar CUDA
        ▼
2. bridge.install()  → pluggable allocator + IAT hooks + patch mem_get_info
        │
        ▼
3. runpy do script alvo  (argv e sys.path preservados como "python script.py")
        │
        ▼
4. [só ComfyUI] placement.install() → wrappers de load/free + prefetch síncrono
                no início de execution.execute (o event loop NÃO ciclo durante
                a execução do workflow, por isso o prefetch é síncrono no boundary)
```

**Dual-track (v3/ComfyUI):** a visão *virtual* mantém a semântica de admissão
do v2 em produção (nada muda na política do ComfyUI), enquanto as chaves
`physical_bytes.*` (NVML) dão o consumo real — sinal honesto para as futuras
fases de política de calor.

## Configuração

Flags do runner (antes do script):

| Flag | Env var | Default | Efeito |
|---|---|---|---|
| `--umc-vram N` | — | 48 | VRAM virtual (GB) |
| `--umc-ram N` | — | 48 | tier de RAM (GB) |
| `--umc-pagefile N` | — | 40 | tier de pagefile (GB) |
| `--umc-priority P` | — | balanced | `balanced` / `max-performance` / `max-capacity` |
| `--dll-dir D` | `UMC_V3_DLL_DIR` | auto | directório das `.dll` |
| `--cwd D` | — | actual | chdir antes de correr o script |
| `--install-test` | — | — | instala, diagnostica, sai |
| (exe) | `UMC_GLOBAL_PYTHON` | auto | aponta o exe a um Python específico |
| (logs) | `UMC_V3_LOG_DIR` | `logs\` / `UMC_global_logs\` | directório dos logs |
| (v3) | `UMC_V3_PREFETCH=0` | 1 | desliga o prefetch do ComfyUI |

## Validação (2026-10-08, nesta máquina)

| Teste | Resultado |
|---|---|
| `--install-test` (bat e exe) | `INSTALL-TEST OK` · `umc_v2_is_active=1` **fora do ComfyUI** · virtual 48 GB + física 12 GB (NVML) |
| **14 GB alocados numa GPU de 12 GB** | sem OOM · RAM 20.7→34.7 GB (+14.0 = spill para o tier de RAM) · matmul correcto · libertação limpa |
| Args da app, `--umc-vram 40`, `--cwd` | todos recebidos/honrados pelo script alvo |
| `UMCglobal.exe` modo disco / embutido / `UMC_GLOBAL_PYTHON` | `exit 0` nos três (DLLs do `_MEIPASS\build`, logs junto do exe) |
| ComfyUI v3 — workflow 7 nós ×3 | `STATUS=success` ×3 · prefetch `n=3 size=1.99GB 1409ms [AutoencoderKL, SD1ClipModel, BaseModel]` no nó 2 · nós seguintes `SKIP (modelos ja carregados)` · loads reactivos 23–33 ms (no-ops) |
| `UMCv3.exe --install-test` | `INSTALL-TEST OK`, todos os hooks activos |
| ruff `F,E9` + `py_compile` | limpo |

## Limitações conhecidas

- **Windows + NVIDIA apenas** (camada nativa e NVML).
- **Filhos de `multiprocessing`/`subprocess` não herdam o bridge** — só o
  processo principal corre com UMC; volta a passar pelo launcher se a app
  fizer inferência num filho.
- **Apps nativas (não-Python)** ficam de fora — a virtualização vive na camada
  PyTorch/Python.
- A visão *virtual* de `mem_get_info` reporta a **capacidade** configurada
  (é a semântica de admissão herdada do v2 em produção); o consumo real por
  tier vê-se no NVML/RAM via `--install-test` ou `umc_diag.py`.
- O ComfyUI **não** está incluído (repo upstream separado) — ver
  [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI).

## Roadmap (fases)

| Fase | Técnica | Estado |
|---|---|---|
| 0 | Diagnóstico baseline (físico vs virtual vs RAM/pagefile) | ✅ |
| 1 | Contabilidade dual-track (admissão virtual + NVML honesto) | ✅ |
| 2 | Prefetch preditivo no boundary dos nós (síncrono) | ✅ |
| 3 | Política de calor/prioridade a partir da telemetria recolhida | ⏳ |
| 4 | Overlap CPU/GPU real (esconder a latência de load) | ⏳ |
| 5 | CUDA VMM nativo (evicção a nível de página) | 💡 |

## Estrutura do repositório

```
umc-global/
├── README.md                     ← este ficheiro
├── ARRANQUE_UMC_V3_TEST.bat      ← launcher do ComfyUI com o v3
├── umc-global/                   ← pacote genérico (qualquer app Python/CUDA)
│   ├── run_umc_global.bat        ← launcher pela fonte
│   ├── build_exe.bat             ← (re)constrói o exe
│   ├── README.md
│   ├── src/                      ← runner + bridge + diag (cópias do v3 testado)
│   ├── build/*.dll               ← DLLs nativas (cópias, ~100 KB)
│   └── dist/UMCglobal.exe        ← executável portátil (onefile, ~8 MB)
├── UMC_v3_strata/                ← integração ComfyUI (dual-track + prefetch)
│   ├── README.md
│   ├── build_exe.bat
│   ├── src/                      ← start + placement + bridge + diag
│   └── dist/UMCv3.exe            ← executável portátil (onefile, ~7.5 MB)
└── tests/                        ← harnesses de validação
```

## Relação com o Strata

As **técnicas** de gestão de modelos do
[Strata](https://github.com/Niko1221/Strata) (contabilidade em camadas,
sinalização honesta de memória, prefetch/prioridades) foram adaptadas de
conceito — **nenhuma linha de código foi usada**: o Strata é uma engine C++
para LLMs; aqui aplicamo-las a um virtualizador CUDA para ComfyUI/apps
PyTorch, com os nossos hooks e DLLs próprios.

## Proveniência e licença

- `umc_*.py` — código deste repositório (derivado do UMC v2 local, já em
  produção nesta máquina).
- `build/*.dll` — camada nativa **pré-compilada do UMC v2**; não há fonte
  nativa disponível.
- ComfyUI, PyTorch e restantes dependências **não** estão incluídos.
- Sem licença declarada — todos os direitos reservados por omissão.
