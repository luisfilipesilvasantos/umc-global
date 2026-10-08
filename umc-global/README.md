# umc-global — UMC para qualquer app Python/CUDA

Pacote autónomo (código-fonte + executável + DLLs nativas) que arranca
**qualquer script Python que use PyTorch/CUDA** com a virtualização UMC activa:
VRAM virtual ampliada (default 48 GB) distribuída por VRAM física → RAM →
pagefile, para correr modelos **maiores que a VRAM física** (apps Gradio de
GitHub/HuggingFace, scripts de inferência, etc.).

É o mesmo motor testado do UMC v3 (`UMC_v3_strata/`), mas **sem dependência do
ComfyUI**: só a camada do bridge (pluggable allocator + IAT hooks + visão
virtual de `mem_get_info`). O prefetch/predição de loads é específico do
ComfyUI e continua no `UMC_v3_strata/`.

## Uso

Pela fonte (recomendado — edições não exigem rebuild):

```bat
run_umc_global.bat [--umc-vram 48] [--umc-ram 48] [--umc-pagefile 40] ^
                   [--umc-priority balanced] meu_script.py [args do script...]
```

Pelo executável (funciona de qualquer directório; procura `python_embeded`
para cima a partir do exe/PATH, ou `UMC_GLOBAL_PYTHON`):

```bat
dist\UMCglobal.exe C:\apps\meu_gradio\app.py --port 7860
```

Verificar que o UMC fica activo sem correr app nenhuma:

```bat
run_umc_global.bat --install-test
```

### Opções UMC

| Opção | Default | O que faz |
|---|---|---|
| `--umc-vram N` | 48 | VRAM virtual em GB |
| `--umc-ram N` | 48 | limite de RAM em GB |
| `--umc-pagefile N` | 40 | limite de pagefile em GB |
| `--umc-priority P` | balanced | `balanced` \| `max-performance` \| `max-capacity` |
| `--dll-dir D` | auto | directório das `.dll` (ou env `UMC_V3_DLL_DIR`) |
| `--cwd D` | actual | muda o directório de trabalho antes de correr o script |
| `--install-test` | — | instala o bridge, diagnostica e sai |

Tudo o resto (a partir da primeira token que não é opção UMC) passa
integralmente para o script alvo — `--help`, `--port`, paths, etc.

### Exemplo real (app Gradio com modelo > VRAM)

```bat
cd C:\apps\sdnext-like
run_umc_global.bat C:\apps\meu_app\app.py --share
```

O app tem de correr com o `python_embeded` deste portable (instala as
dependências dele aí: `python_embeded\python.exe -m pip install ...`).

## Estrutura

```
umc-global/
├── run_umc_global.bat      launcher pela fonte
├── build_exe.bat           (re)constrói dist\UMCglobal.exe
├── README.md
├── src/
│   ├── run_with_umc.py     runner genérico NOVO: env → bridge → runpy do alvo
│   ├── exe_main_global.py  entrada do exe (localiza python + src)
│   ├── umc_common.py       cópia verbatim do UMC v3 (logs)
│   ├── umc_bridge_v3.py    cópia verbatim do UMC v3 (allocator+hooks+visões)
│   └── umc_diag.py         cópia verbatim do UMC v3 (diagnóstico)
├── build/                  DLLs nativas (copiadas de ComfyUI\umc\build)
└── dist/UMCglobal.exe      executável portátil (onefile)
```

- **Logs**: `umc-global\logs\umc_v3.log` (pela fonte) ou
  `UMC_global_logs\` junto do exe (quando os scripts vêm embutidos no exe).
  Env `UMC_V3_LOG_DIR` sobrepõe.
- **Diagnóstico avulsão**: `python_embeded\python.exe src\umc_diag.py --probe|--snapshot|--watch N`.

## Limitações (conhecidas)

- **Subprocessos**: um filho criado por `multiprocessing` (spawn) ou
  `subprocess` de python NÃO herda o bridge — só o processo principal corre com
  UMC. Se a app fizer inferência num filho, volta a passar pelo launcher:
  `run_umc_global.bat filho.py`.
- **Apps nativas (não-Python)**: fora do âmbito — a virtualização vive na
  camada Python (pluggable allocator) + hooks; só a camada de página
  alargada beneficiaria apps nativas.
- **ComfyUI**: use antes o `UMC_v3_strata\` (tem contabilidade dual-track,
  prefetch e testes próprios); `run_umc_global.bat ComfyUI\main.py` também
  funciona mas é o fluxo "v2" sem prefetch.
- **Visão virtual vs real**: `torch.cuda.mem_get_info` apresenta a
  *capacidade* configurada (ex. 48 GB) como livre — é a visão de admissão do
  UMC, idêntica à do v2 em produção (mesmo patch, mesmas DLLs). O consumo real
  por tier vê-se no NVML físico e na RAM (`--install-test` / `umc_diag.py`).
- **Sem GPU NVIDIA/pynvml** a visão física honesta fica indisponível (a
  virtual continua a funcionar).

## Rebuild do executável

```bat
cmd /c build_exe.bat
```

Única dependência: PyInstaller (instalado à primeira vez no `python_embeded`).
As DLLs de `build\` e o `src\` vão embutidos no exe; com fonte em disco ao
lado, o exe usa a fonte de disco (edições valem sem rebuild).

## Validação (2026-10-08, nesta máquina)

- `run_umc_global.bat --install-test` → `INSTALL-TEST OK`, `umc_v2_is_active=1`
  **fora** do ComfyUI, virtual 48GB + física 12GB (NVML).
- Alloc test: **14GB alocados numa GPU de 12GB** sem OOM — RAM 20.7→34.7GB
  (+14.0, tier de RAM do UMC), matmul correcto, libertação limpa.
- Args do script alvo (`--port 7860 --share ...`), `--umc-vram 40` e `--cwd`
  passados/honrados; `umc_diag.py` avulso (`--probe`) funcional.
- `dist\UMCglobal.exe`: modo disco, modo embutido (exe sozinho noutro directório,
  DLLs do `_MEIPASS\build`, logs em `UMC_global_logs\`) e `UMC_GLOBAL_PYTHON`
  — tudo `exit 0`.

## Proveniência

- Camada nativa (`build\*.dll`): pré-compilada do UMC v2 — não há fonte
  nativa disponível nesta máquina (ver `UMC_v3_strata\README.md`).
- `umc_*.py`: cópias verbatim de `UMC_v3_strata\src\` (motor validado em
  2026-10), para o pacote ser copiável de forma independente.
- `run_with_umc.py` / `exe_main_global.py`: novos, para este pacote.
