"""UMC global - arranca QUALQUER script Python com o UMC activo.

O UMC tem de ser instalado ANTES de o torch importar CUDA, por isso não dá
para fazer "import umc_bridge" de dentro do script alvo: este runner É o
processo — instala o bridge e executa o script com runpy, exactamente como o
start_umc_v2 fazia ao main.py do ComfyUI, mas sem dependências do ComfyUI.
Serve apps Gradio/HuggingFace/scripts de inferência que carreguem modelos
maiores que a VRAM física.

Uso:
    run_with_umc.py [opções] <script.py> [argumentos do script...]

Opções UMC (tudo o resto passa integralmente para o script alvo):
    --umc-vram N        VRAM virtual em GB          (default 48)
    --umc-ram N         limite de RAM em GB         (default 48)
    --umc-pagefile N    limite de pagefile em GB    (default 40)
    --umc-priority P    balanced | max-performance | max-capacity
    --dll-dir D         directório das .dll nativas (ou env UMC_V3_DLL_DIR)
    --cwd D             mudar o directório de trabalho antes de correr
    --install-test      instalar o bridge, diagnosticar e sair

Ordem crítica (invariantes UMC): env -> bridge.install (antes de
torch.cuda) -> sys.argv/sys.path -> runpy.run_path(script).
"""
import os
import runpy
import sys

_FLOAT_FLAGS = {"--umc-vram": "vram", "--umc-ram": "ram", "--umc-pagefile": "pagefile"}
_STR_FLAGS = {"--umc-priority": "priority", "--dll-dir": "dll_dir", "--cwd": "cwd"}
_PRIORITIES = ("balanced", "max-performance", "max-capacity")


def parse_args(argv):
    """Separa as opções UMC do que é do script alvo.

    Varre manualmente (em vez de argparse) porque os argumentos do script alvo
    podem ter as mesmas formas (-h, --port 7860, ficheiros começados a --):
    a primeira token que não é opção UMC nossa termina a nossa parse.
    """
    opts = {"vram": 48.0, "ram": 48.0, "pagefile": 40.0, "priority": "balanced",
            "dll_dir": None, "cwd": None, "install_test": False}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        key, has_eq, val = tok.partition("=")
        if key in _FLOAT_FLAGS or key in _STR_FLAGS:
            if not has_eq:
                i += 1
                if i >= len(argv):
                    sys.exit("[UMC global] ERRO: {} precisa de valor".format(key))
                val = argv[i]
            if key in _FLOAT_FLAGS:
                try:
                    val = float(val)
                except ValueError:
                    sys.exit("[UMC global] ERRO: valor invalido em {}".format(tok))
            # nota: nao usar _FLOAT.get(key, _STR[key]) - o default era
            # avaliado mesmo quando key esta em _FLOAT (KeyError eager)
            opts[_FLOAT_FLAGS.get(key) or _STR_FLAGS[key]] = val
        elif tok == "--install-test":
            opts["install_test"] = True
        elif tok in ("-h", "--help"):
            print(__doc__)  # noqa: T201
            sys.exit(0)
        elif tok.startswith("--umc-") or key == "--dll-dir":
            sys.exit("[UMC global] ERRO: opcao desconhecida {} (tenta --help)".format(tok))
        else:
            break  # primeira token que não é nossa = o script alvo
        i += 1
    if opts["priority"] not in _PRIORITIES:
        sys.exit("[UMC global] ERRO: --umc-priority invalido ({})".format(opts["priority"]))
    return opts, argv[i:]


def main():
    opts, target = parse_args(sys.argv[1:])
    script = None
    if target:
        script = os.path.abspath(target[0])
        if not os.path.isfile(script):
            print("[UMC global] ERRO: script nao encontrado: {}".format(target[0]))  # noqa: T201
            sys.exit(1)
    elif not opts["install_test"]:
        print(__doc__)  # noqa: T201
        sys.exit(2)

    # Ambiente ANTES de o torch importar (regras v2, iguais ao start_umc_v2):
    # PYTORCH_*_ALLOC_CONF com backend:cudaMallocAsync conflita com o
    # pluggable allocator; sageattention desactiva-se pela mesma razão.
    os.environ.pop("PYTORCH_CUDA_ALLOC_CONF", None)
    os.environ.pop("PYTORCH_ALLOC_CONF", None)
    os.environ["DISABLE_SAGEATTENTION"] = "1"
    os.environ["UMC_V2"] = "1"

    # sys.path como "python script.py": pasta do script primeiro, depois a
    # nossa (o script tem precedência em colisões de nomes de módulo). A
    # nossa entra aqui porque o python_embeded usa python312._pth e NÃO poe
    # a pasta do script corrente em sys.path[0] (daí não haver import de
    # irmãos no topo do módulo).
    this_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, this_dir)
    script_dir = os.path.dirname(script) if script else None
    if script_dir and script_dir != this_dir:
        sys.path.insert(0, script_dir)

    if opts["cwd"]:
        os.chdir(opts["cwd"])

    # O script tem de ver o argv dele próprio (flags de porta, model paths...).
    if script:
        sys.argv = [script] + target[1:]

    import umc_bridge_v3

    ok = umc_bridge_v3.install(
        dll_dir=opts["dll_dir"],
        vram_gb=opts["vram"],
        ram_gb=opts["ram"],
        pagefile_gb=opts["pagefile"],
        priority=opts["priority"],
    )
    if not ok:
        print("[UMC global] ERRO: instalacao do bridge falhou")  # noqa: T201
        sys.exit(3)

    if opts["install_test"]:
        import umc_diag
        dlls_ok = umc_diag.install_test_report(dll_dir=opts["dll_dir"])
        umc_bridge_v3.verify()
        print("[UMC global] INSTALL-TEST {}".format("OK" if dlls_ok else "PARCIAL"))  # noqa: T201
        sys.exit(0 if dlls_ok else 6)

    umc_bridge_v3.log("runner", "arrancar {} | args={}".format(script, sys.argv[1:]))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
