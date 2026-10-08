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
   admissão mudou — a política de unload só mudou na Fase 3, com dados.
2. **Prefetch preditivo pelo grafo (Fase 2)** — o ComfyUI conhece o workflow
   completo antes de executar, logo a previsão é exacta: a cada nó, olhamos
   para os nós pendentes, andamos para trás pelas inputs até aos loaders já
   executados e carregamos os ModelPatcher (UNet/CLIP/VAE) que ainda não estão
   em GPU, enquanto o nó actual trabalha. Sobrescreve só quando a memória
   virtual livre couber o necessário + 2GB de margem (senão deixa o load
   reagir como dantes). Pelo calor: carrega os modelos mais quentes primeiro.
3. **Telemetria de loads (Fase 0)** — cada `load_models_gpu`/`free_memory` é
   registado com nº de modelos, tamanho, duração e para que prompt, em
   `logs\umc_v3.log`. É a base que a Fase 3 consome.
4. **Política de calor + integridade (Fase 3)** — calor online por modelo
   (decay 0.7/prompt, histerese 1.5, perfil persistido com sha256 + `.bak`),
   evicção *guardada* dos modelos frios sob pressão virtual e canário de
   integridade verificado a cada prompt com self-heal (ver secção própria).

## Estrutura

```
UMC_v3_strata\
  src\
    umc_common.py       logs e directórios partilhados
    umc_bridge_v3.py    bridge dual-track (evolução do umc_bridge v2)
    umc_placement.py    hooks de contabilidade + prefetch + evicção guardada
    umc_heat.py         calor online por modelo + perfil persistido (Fase 3)
    umc_integrity.py    canário de integridade + self-heal + roundtrip (Fase 3)
    umc_diag.py         diagnóstico: --probe / --snapshot / --watch N
    start_umc_v3.py     launcher (contrato do start_umc_v2 + --install-test/--no-prefetch)
    exe_main.py         bootstrap do executável portátil
  build_exe.bat         constrói dist\UMCv3.exe
  dist\UMCv3.exe        executável portátil (one-file)
  UMCv3.spec, build_tmp\  artefactos do PyInstaller (recriáveis)
  logs\                 umc_v3.log (+ umc_v3_diag.csv com --watch,
                        + umc_heat_profile.json com o calor persistido)
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
| `UMC_V3_HEAT_POLICY=0` | desliga calor/epoch/evicção (a telemetria continua) |
| `UMC_V3_CANARY=0` | desliga o canário de integridade |
| `UMC_V3_CANARY_MB` | tamanho do canário em MiB (default 16) |
| `UMC_V3_DLL_DIR` | directório alternativo dos `.dll` do UMC |
| `UMC_V3_LOG_DIR` | directório dos logs (default: `UMC_v3_strata\logs`) |

Qualquer erro nos hooks é apanhado e registado: no pior caso perde-se o
prefetch/telemetria, nunca arranca o ComfyUI nem parte um workflow.

## Complementaridade

O ComfyUI traz `comfy.model_prefetch` (prefetch de pesos *dentro* de um modelo
durante o sampling). O prefetch do v3 actua *entre* modelos (admissão no limite
nó→nós). São camadas diferentes e convivem.

## Fase 3 — calor, evicção guardada e integridade

**Calor (`umc_heat.py`)** — por prompt submetido, cada modelo carregado
recebe `heat += usage; usage *= 0.7` (integral exponencialmente ponderada do
uso recente). A identidade é `"<classe>:<bytes>"` do ModelPatcher (dois
checkpoints da mesma arquitectura e tamanho partilham calor — partilham
também o custo de carregamento). O perfil (`logs\umc_heat_profile.json`)
sobrevive a sessões: escrita atómica (`.tmp` + rename), sha256 interno das
entradas e `.bak` da geração anterior; ficheiro corrompido cai no `.bak`.
Exposto no `memory_stats()` como `heat_bytes.all.current/.peak` (só leitura).

**Evicção guardada** — antes de o `free_memory` do ComfyUI actuar, se a
VRAM *virtual* estiver apertada (< 4 GiB livres) descarregam-se primeiro os
modelos *frios* (calor 0, ou calor + 1.5 ≤ o mais quente), por ordem de
(calor, mais offloaded, menor refcount, menor tamanho). Máximo 3 por
chamada, cooldown de 30 s, alvo `pedido + 2 GiB` (mínimo 4 GiB). Nunca toca
em: `for_dynamic`, `keep_loaded`, `DISABLE_SMART_MEMORY`, modelos recém
carregados (anti-thrash) nem modelos do prompt corrente. Assim o free_memory
do ComfyUI vê espaço livre e não precisa de descarregar os quentes.

**Integridade (`umc_integrity.py`)** — as camadas, honestamente:
1. *ECC*: não existe — a RTX 3060 (GA106) não tem ECC activável. Não há
   correção automática no hardware.
2. *Detecção*: um canário (bloco de 16 MiB com conteúdo aleatório conhecido,
   alocado só sob pressão física — é assim o candidato natural de spill)
   é relido e comparado por blake2b **a cada prompt**. Divergência =
   corrupção ou leitura stale no caminho GPU→RAM→GPU.
3. *Reparação*: divergência → `unload_all_models()` — os pesos voltam do
   disco (safetensors = réplica de verdade) na próxima utilização — e o
   canário é recriado. Máximo 3 heals por sessão; depois desliga-se e avisa
   no log. Verificação ponta-a-ponta: `python -c "import sys;
   sys.path.insert(0, r'UMC_v3_strata\src'); import umc_integrity;
   sys.exit(0 if umc_integrity.roundtrip_test() else 1)"` (exit ≠ 0 em
   falha).

**Limitação honesta**: a transferência em si acontece dentro dos `.dll`
nativos (sem fonte) — não podemos inserir checksums *no* transporte. O
protocolo completo (hash por bloco na tabela de residência, escrita→
verificação→commit antes de libertar a fonte, invalidação por `cuMemUnmap`)
é a especificação da Fase 5, quando houver código nativo próprio. Por agora
medimos o resultado de fora: detecção + réplica em disco + self-heal.

## Fases seguintes (plano aprovado)

- ~~**Fase 3**~~ — **feito**: política de calor, evicção guardada e
  integridade (secção acima).
- **Fase 4** — overlap CPU/GPU (carregar no stream lateral durante compute).
- **Fase 5** — reescrita nativa com CUDA VMM (requer código próprio; os .dll
  actuais são fechados) + protocolo de integridade dentro do transporte.

## Notas de build

- `build_exe.bat` usa `python_embeded\python.exe -m pip install pyinstaller`
  (dependência de build apenas; o ComfyUI nunca a importa). Para remover:
  `python_embeded\python.exe -m pip uninstall pyinstaller altgraph pefile pywin32-ctypes`.
- Os avisos `[UMC v2] ...` no arranque vêm do `.dll` nativo (printf próprio),
  não do Python — iguais aos do v2.
