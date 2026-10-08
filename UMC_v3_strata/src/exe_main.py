"""UMC v3 - entrada do executável UMCv3.exe (PyInstaller onefile).

O exe é só um launcher portátil: localiza a raiz do install (python_embeded +
ComfyUI), encontra os scripts (fonte em disco primeiro — edições valem sem
reconstruir o exe; senão os embutidos) e arranca o runtime real:

    <python_embeded\\python.exe> start_umc_v3.py --comfyui-dir <raiz>/ComfyUI <args...>

Assim o exe corre em qualquer raiz portable sem instalação nenhuma.
"""
import os
import shutil
import subprocess
import sys


def _find_root(start):
    """Procura a raiz do portable (python_embeded/python.exe + ComfyUI/main.py)."""
    d = os.path.abspath(start)
    for _ in range(5):
        if (os.path.isfile(os.path.join(d, "python_embeded", "python.exe"))
                and os.path.isfile(os.path.join(d, "ComfyUI", "main.py"))):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _scripts_dir(exe_dir):
    """Directório dos scripts: fonte em disco primeiro, embutido como fallback."""
    here = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, "frozen", False) else None
    cands = [
        os.path.join(exe_dir, "UMC_v3_strata", "src"),
        os.path.join(exe_dir, "src"),
        os.path.join(os.path.dirname(exe_dir), "src"),
        os.path.join(os.path.dirname(exe_dir), "UMC_v3_strata", "src"),
    ]
    if here:
        cands.insert(0, here)
    for c in cands:
        if os.path.isfile(os.path.join(c, "start_umc_v3.py")):
            return c, "disco"
    bundled = os.path.join(getattr(sys, "_MEIPASS", exe_dir), "src")
    if os.path.isfile(os.path.join(bundled, "start_umc_v3.py")):
        return bundled, "embutido"
    return None, None


def _find_python(*starts):
    for start in starts:
        if not start:
            continue
        d = os.path.abspath(start)
        for _ in range(5):
            p = os.path.join(d, "python_embeded", "python.exe")
            if os.path.isfile(p):
                return p
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return shutil.which("python") or shutil.which("python3")


def main():
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    argv = sys.argv[1:]

    comfy_dir = None
    for i, a in enumerate(argv):
        if a == "--comfyui-dir" and i + 1 < len(argv):
            comfy_dir = argv[i + 1]
        elif a.startswith("--comfyui-dir="):
            comfy_dir = a.split("=", 1)[1]

    root = None
    if comfy_dir:
        root = _find_root(comfy_dir) or _find_root(os.path.dirname(os.path.abspath(comfy_dir)))
    root = root or _find_root(exe_dir) or _find_root(os.getcwd())
    if root is None and comfy_dir is None:
        print("[UMC v3] ERRO: raiz portable (python_embeded + ComfyUI) nao encontrada; "  # noqa: T201
              "passa --comfyui-dir <dir>")
        return 1
    if comfy_dir is None:
        comfy_dir = os.path.join(root, "ComfyUI")
        argv = ["--comfyui-dir", comfy_dir] + argv

    python = _find_python(comfy_dir, root, exe_dir)
    if not python:
        print("[UMC v3] ERRO: python nao encontrado (nem python_embeded nem PATH)")  # noqa: T201
        return 1

    scripts, origem = _scripts_dir(exe_dir)
    if scripts is None:
        print("[UMC v3] ERRO: start_umc_v3.py nao encontrado (nem em disco nem embutido no exe)")  # noqa: T201
        return 1
    if origem == "embutido":
        # Scripts embutidos vivem em directório temporário (_MEIPASS): os logs
        # ficam ao lado do exe para não se perderem quando o temp é limpo.
        os.environ.setdefault("UMC_V3_LOG_DIR", os.path.join(exe_dir, "UMC_v3_logs"))

    start = os.path.join(scripts, "start_umc_v3.py")
    print("[UMC v3] python={} | scripts={} ({})".format(python, scripts, origem), flush=True)  # noqa: T201
    return subprocess.run([python, start] + argv).returncode


if __name__ == "__main__":
    sys.exit(main())
