# UMC v3 — técnicas Strata aplicadas ao UMC (ComfyUI)

Pasta **nova e separada**: o UMC v2 (`ARRANQUE_UMC_v2_FIX.bat`, `ComfyUI\umc\`)
continua intacto e a funcionar como dantes. Esta versão aplica ao UMC as
*técnicas* de gestão de modelos do Strata (https://github.com/Niko1221/Strata)
— adaptadas ao contexto do ComfyUI, **sem qualquer código do Strata** (o Strata
é um motor C++ de LLM; aqui só se aproveitam as ideias de placement).

## O que mudou face ao v2

1. **Contabilidade dual-track (Fase 1)** — o v2 só expunha a visão *virtual*
   (ex. 48GB) ao ComfyUI; o v3 mantém exactamente essa admissão (é ela que
   permite carregar modelos maiores que a VRAM) e acrescenta a visão *física
   real* via pynvml/NVML: `physical_total()/free()/used()` no bridge e as
   chaves `physical_bytes.all.*` no `memory_stats()`. Nenhuma semântica de
   admissão mudou — mudar a política de unload é a Fase 3, e faz-se com dados.
2. **Prefetch preditivo pelo grafo (Fase 2)** — o ComfyUI conhece o workflow
   completo antes de executar, logo a previsão é exacta: a cada nó, olhamos
   para os nós pendentes, andamos para trás pelas inputs até aos loaders já
   executados e carregamos os ModelPatcher (UNet/CLIP/VAE) que ainda não estão
   em GPU, enquanto o nó actual trabalha. Sobrescreve só quando a memória
   virtual livre couber o necessário + 2GB de margem (senão deixa o load
   reagir como dantes).
3. **Telemetria de loads (Fase 0)** — cada `load_models_gpu`/`free_memory` é
   registado com nº de modelos, tamanho, duração e para que prompt, em
   `logs\umc_v3.log`. É a base para a Fase 3 (política de calor/prioridade).

## Estrutura

```
UMC_v3_strata\
  src\
    umc_common.py       logs e directórios partilhados
    umc_bridge_v3.py    bridge dual-track (evolução do umc_bridge v2)
    umc_placement.py    hooks de contabilidade + prefetch preditivo
    umc_diag.py         diagnóstico: --probe / --snapshot / --watch N
    start_umc_v3.py     launcher (contrato do start_umc_v2 + --install-test/--no-prefetch)
    exe_main.py         bootstrap do executável portátil
  build_exe.bat         constrói dist\UMCv3.exe
  dist\UMCv3.exe        executável portátil (one-file)
  UMCv3.spec, build_tmp\  artefactos do PyInstaller (recriáveis)
  logs\                 umc_v3.log (+ umc_v3_diag.csv com --watch)
```

Os `.dll` nativos são os do próprio UMC v2 (`ComfyUI\umc\build`) — reutilizados,
não copiados; não existe fonte nativa. O bridge resolve esse directório sozinho
(env `UMC_V3_DLL_DIR` para forçar outro).

## Como testar

**1. Teste rápido sem arrancar ComfyUI** (bridge + hooks + probe + estado de memória):

```
python_embeded\python.exe UMC_v3_strata\src\start_umc_v3.py ^
  --umc-vram 48 --umc-ram 48 --umc-pagefile 40 --umc-priority balanced ^
  --comfyui-dir "%CD%\ComfyUI" --install-test
```

Saída esperada: `INSTALL-TEST OK` e exit code 0 (`PARCIAL`/exit 6 = ver linhas).

**2. Arranque real**: `ARRANQUE_UMC_V3_TEST.bat` (flags idênticas ao
`ARRANQUE_UMC_v2_FIX.bat`). Depois de gerar qualquer workflow, ver
`UMC_v3_strata\logs\umc_v3.log` — linhas `load`, `free` e `prefetch`.

**3. Via executável** (portabilidade): ver abaixo.

## Executável portátil

`UMC_v3_strata\dist\UMCv3.exe` é um launcher *one-file*: localiza sozinho a
raiz do portable (`python_embeded\python.exe` + `ComfyUI\main.py`, procurando
para cima a partir do próprio exe), encontra os scripts (usa a fonte em disco
se existir — edições valem sem rebuild; senão usa os embutidos no exe) e arranca
o `python_embeded\python.exe` de lá com os teus argumentos:

```
UMCv3.exe --umc-vram 48 --umc-ram 48 --umc-pagefile 40 --comfyui-dir <dir\ComfyUI> [flags ComfyUI...]
```

Sem `--comfyui-dir` ele infere a raiz onde está. Copia só o exe para a raiz de
qualquer outro install portable e funciona lá (os scripts vêm embutidos; os logs
ficam em `UMC_v3_logs\` ao lado do exe). Rebuild: `UMC_v3_strata\build_exe.bat`
(instala o pyinstaller uma vez, só como ferramenta de build).

## Variáveis e kill-switches

| Variável | Efeito |
|---|---|
| `UMC_V3_PREFETCH=0` | desliga o prefetch (ou usa `--no-prefetch`) |
| `UMC_V3_DLL_DIR` | directório alternativo dos `.dll` do UMC |
| `UMC_V3_LOG_DIR` | directório dos logs (default: `UMC_v3_strata\logs`) |

Qualquer erro nos hooks é apanhado e registado: no pior caso perde-se o
prefetch/telemetria, nunca arranca o ComfyUI nem parte um workflow.

## Complementaridade

O ComfyUI traz `comfy.model_prefetch` (prefetch de pesos *dentro* de um modelo
durante o sampling). O prefetch do v3 actua *entre* modelos (admissão no limite
nó→nós). São camadas diferentes e convivem.

## Fases seguintes (plano aprovado)

- **Fase 3** — política de calor/prioridade usando o sinal físico honesto
  (`physical_bytes.*`, `umc_v2_stats_vram_used`, contagem de loads do log).
- **Fase 4** — overlap CPU/GPU (carregar no stream lateral durante compute).
- **Fase 5** — reescrita nativa com CUDA VMM (requer código próprio; os .dll
  actuais são fechados).

## Notas de build

- `build_exe.bat` usa `python_embeded\python.exe -m pip install pyinstaller`
  (dependência de build apenas; o ComfyUI nunca a importa). Para remover:
  `python_embeded\python.exe -m pip uninstall pyinstaller altgraph pefile pywin32-ctypes`.
- Os avisos `[UMC v2] ...` no arranque vêm do `.dll` nativo (printf próprio),
  não do Python — iguais aos do v2.
