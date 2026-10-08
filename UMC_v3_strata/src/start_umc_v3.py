"""UMC v3 - launcher do ComfyUI com bridge dual-track + placement/prefetch.

Contrato de linha de comandos igual ao start_umc_v2 (mesmos --umc-* e
passthrough para o main.py), acrescentado de:
  --no-prefetch    desliga o prefetch preditivo
  --install-test   instala bridge+placement, imprime diagnóstico e sai

Ordem crítica (invariantes UMC):
  argv final -> env (antes do torch) -> bridge.install (antes de torch.cuda)
  -> cli_args parseado (ANTES de importar execution, senão main.py ficava
  sem as flags) -> placement.install -> runpy main.py.
"""
import argparse
import os
import runpy
import sys


def main():
    parser = argparse.ArgumentParser(description="UMC v3 launcher para ComfyUI")
    parser.add_argument("--umc-vram", type=float, default=48.0, help="Virtual VRAM limit in GB")
    parser.add_argument("--umc-ram", type=float, default=48.0, help="RAM limit in GB")
    parser.add_argument("--umc-pagefile", type=float, default=40.0, help="Pagefile limit in GB")
    parser.add_argument("--umc-priority", type=str, default="balanced",
                        help="balanced | max-performance | max-capacity")
    parser.add_argument("--comfyui-dir", type=str, required=True, help="Path to ComfyUI directory")
    parser.add_argument("--no-prefetch", action="store_true",
                        help="desliga o prefetch preditivo (ou env UMC_V3_PREFETCH=0)")
    parser.add_argument("--install-test", action="store_true",
                        help="instalar hooks, diagnosticar e sair sem arrancar ComfyUI")
    args, comfy_args = parser.parse_known_args()

    this_dir = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(args.comfyui_dir, "main.py")
    if not os.path.exists(main_py):
        print("[UMC v3] ERROR: main.py nao encontrado em {}".format(main_py))  # noqa: T201
        sys.exit(1)

    # O argv tem de ficar EXATAMENTE como o main.py o veria ANTES de qualquer
    # import que toque em comfy.cli_args (o parse acontece no import; se
    # acontecesse com args default, as flags do ComfyUI perderam-se tudo).
    sys.argv = [main_py] + comfy_args

    # Ambiente antes de o torch ser importado (regras v2).
    os.environ.pop("PYTORCH_CUDA_ALLOC_CONF", None)
    os.environ.pop("PYTORCH_ALLOC_CONF", None)
    os.environ["DISABLE_SAGEATTENTION"] = "1"
    os.environ["UMC_V2"] = "1"

    # v2 só metia os scripts no path; o v3 importa comfy.* cedo (hooks), por
    # isso a raiz do ComfyUI também. this_dir por último fica em sys.path[0].
    for p in (args.comfyui_dir, this_dir):
        if p not in sys.path:
            sys.path.insert(0, p)

    # 1. Bridge ANTES de qualquer operação torch.cuda.
    import umc_bridge_v3

    dll_dir = os.path.join(args.comfyui_dir, "umc", "build")
    ok = umc_bridge_v3.install(
        dll_dir=dll_dir if os.path.isdir(dll_dir) else None,
        vram_gb=args.umc_vram,
        ram_gb=args.umc_ram,
        pagefile_gb=args.umc_pagefile,
        priority=args.umc_priority,
    )
    if not ok:
        print("[UMC v3] ERROR: instalacao do bridge falhou")  # noqa: T201
        sys.exit(3)

    # VRAM física no arranque (sinal honesto da Fase 1/3); LOW se < 512 MiB.
    from umc_common import log
    pfree = umc_bridge_v3.physical_free()
    if pfree is not None:
        msg = "{} MiB de VRAM fisicos livres no arranque".format(pfree // 1024 ** 2)
        log("start", ("LOW: " if pfree < 512 * 1024 ** 2 else "") + msg)

    # 2. cli_args com o argv final, antes de importar execution (que o lê).
    import comfy.options
    comfy.options.enable_args_parsing()
    try:
        import comfy.cli_args  # noqa: F401
    except SystemExit:
        print("[UMC v3] ERROR: argumentos recusados pelo ComfyUI (ver mensagem acima)")  # noqa: T201
        sys.exit(4)

    # Com UMC o backend tem de ser sempre o pluggable, mas o cuda_malloc.py do
    # ComfyUI (>= 0.34) ja nao honra a env UMC_V2 e, quando o argv vem SEM
    # --disable-cuda-malloc (ex.: UMCv3.exe de duplo-clique), força
    # PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync depois do bridge instalado.
    # O torch re-parseia a config em runtime (ex.: torch.istft, SAM3) e faz
    # assert "cudaMallocAsync != pluggable" contra o allocator do UMC.
    comfy.cli_args.args.disable_cuda_malloc = True

    # 3. Placement: contabilidade de loads + prefetch preditivo.
    import umc_placement
    umc_placement.install(prefetch=not args.no_prefetch)

    if args.install_test:
        # Dispara o trigger adiado (mesma chamada que o main.py:68-80 faria)
        # para validar o caminho real de instalação diferida dos hooks.
        umc_placement.fire_aimdo_init_for_test()
        import umc_diag
        dlls_ok = umc_diag.install_test_report(dll_dir=dll_dir if os.path.isdir(dll_dir) else None)
        print("[UMC v3] " + umc_placement.summary())  # noqa: T201
        hooks_ok = all(umc_placement._hooks.values())  # noqa: SLF001 - relatório do próprio módulo
        print("[UMC v3] INSTALL-TEST {}".format("OK" if (dlls_ok and hooks_ok) else "PARCIAL (ver linhas acima)"))  # noqa: T201
        sys.exit(0 if (dlls_ok and hooks_ok) else 6)

    runpy.run_path(main_py, run_name="__main__")


if __name__ == "__main__":
    main()
