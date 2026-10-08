"""UMC global - entrada do executável UMCglobal.exe (PyInstaller onefile).

O exe é só um launcher portátil: localiza um python (python_embeded do
portable ou PATH), localiza os scripts (fonte em disco primeiro — edições
valem sem reconstruir o exe; senão os embutidos) e arranca o runner real:

    <python> src\\run_with_umc.py <opções UMC> <script.py> <args...>

Sem dependência do ComfyUI: serve qualquer install/portable que tenha um
python com torch instalado (define UMC_GLOBAL_PYTHON para apontar a um
python específico).
"""
import os
import shutil
import subprocess
import sys


def _find_python(*starts):
    for start in starts:
        if not start:
            continue
        d = os.path.abspath(start)
        for _ in range(6):
            p = os.path.join(d, "python_embeded", "python.exe")
            if os.path.isfile(p):
                return p
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return shutil.which("python") or shutil.which("python3")


def _scripts_dir(exe_dir):
    """Directório dos scripts: fonte em disco primeiro, embutido como fallback."""
    here = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, "frozen", False) else None
    cands = [
        os.path.join(exe_dir, "src"),
        os.path.join(exe_dir, "umc-global", "src"),
        os.path.join(os.path.dirname(exe_dir), "src"),
        os.path.join(os.path.dirname(exe_dir), "umc-global", "src"),
    ]
    if here:
        cands.insert(0, here)
    for c in cands:
        if os.path.isfile(os.path.join(c, "run_with_umc.py")):
            return c, "disco"
    bundled = os.path.join(getattr(sys, "_MEIPASS", exe_dir), "src")
    if os.path.isfile(os.path.join(bundled, "run_with_umc.py")):
        return bundled, "embutido"
    return None, None


def main():
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    argv = sys.argv[1:]
    if not argv:
        print("[UMC global] Uso: UMCglobal.exe [--umc-vram N ...] <script.py> [args...]")  # noqa: T201
        print("[UMC global] (o exe só localiza o python e o src; ver README.md)")  # noqa: T201
        return 2

    python = os.environ.get("UMC_GLOBAL_PYTHON") or _find_python(exe_dir, os.getcwd())
    if not python:
        print("[UMC global] ERRO: python nao encontrado (nem python_embeded nem PATH; "  # noqa: T201
              "define UMC_GLOBAL_PYTHON)")
        return 1

    scripts, origem = _scripts_dir(exe_dir)
    if scripts is None:
        print("[UMC global] ERRO: run_with_umc.py nao encontrado (nem em disco nem embutido no exe)")  # noqa: T201
        return 1
    if origem == "embutido":
        # Scripts embutidos vivem em directório temporário (_MEIPASS): os logs
        # ficam ao lado do exe para não se perderem quando o temp é limpo.
        os.environ.setdefault("UMC_V3_LOG_DIR", os.path.join(exe_dir, "UMC_global_logs"))

    print("[UMC global] python={} | scripts={} ({})".format(python, scripts, origem), flush=True)  # noqa: T201
    return subprocess.run([python, os.path.join(scripts, "run_with_umc.py")] + argv).returncode


if __name__ == "__main__":
    sys.exit(main())
