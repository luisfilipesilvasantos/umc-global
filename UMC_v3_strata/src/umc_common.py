"""UMC v3 - utilitários comuns (directórios de log e logging unificado).

Decisão: os logs vivem fora do ComfyUI/ (repo git com regras próprias) para
nunca poluir o repositório upstream e para sobreviverem a updates do ComfyUI.
"""
import os
import sys
import time

LOG_NAME = "umc_v3.log"


def package_dir():
    """Directório base do pacote UMC v3.

    Em modo congelado (PyInstaller) __file__ aponta para o directório temporário
    (_MEIPASS), por isso usamos a directoria do executável — senão os logs e a
    procura de scripts ficariam presos numa cache temporária que morre no fim.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def log_dir():
    d = os.environ.get("UMC_V3_LOG_DIR")
    if not d:
        if getattr(sys, "frozen", False):
            d = os.path.join(package_dir(), "UMC_v3_logs")
        else:
            base = package_dir()
            # src/ -> UMC_v3_strata/logs
            parent = os.path.dirname(base) if os.path.basename(base).lower() == "src" else base
            d = os.path.join(parent, "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def log(tag, msg, also_print=True):
    line = "[UMC v3][{}][{}] {}".format(tag, time.strftime("%H:%M:%S"), msg)
    if also_print:
        print(line)  # noqa: T201 - código local, fora do lint do repo ComfyUI/
    try:
        with open(os.path.join(log_dir(), LOG_NAME), "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def fmt_gb(n_bytes):
    if n_bytes is None:
        return "n/d"
    return "{:.2f}GB".format(n_bytes / 1024 ** 3)
