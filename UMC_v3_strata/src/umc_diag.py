"""UMC v3 - diagnóstico de baseline: físico vs virtual vs RAM/pagefile.

Modos:
  --snapshot   imprime o estado actual (default)
  --probe      carrega os .dll e verifica os exports (chamadas mínimas)
  --watch N    amostra a cada N segundos -> logs/umc_v3_diag.csv
  --dll-dir D  directório dos .dll (senão env UMC_V3_DLL_DIR / instalação v2)

A visão virtual (ex. 48GB) só existe em processos com o UMC activo (bridge
instalado); noutros processos o diag mostra só físico/RAM/pagefile e deixa as
colunas virtuais vazias no CSV.
"""
import argparse
import ctypes
import os
import time

from umc_common import fmt_gb, log_dir

# Exports confirmados por scan binário dos .dll em ComfyUI\umc\build.
# O probe verifica presença via ctypes (GetProcAddress); só is_active é chamado.
_EXPORTS = {
    "VirtualGpuMemory.dll": [
        "umc_v2_init", "umc_v2_is_active", "umc_v2_mem_info",
        "umc_v2_stats_vram_used", "umc_v2_alloc", "umc_v2_free",
        "umc_v2_register", "umc_v2_unregister", "umc_v2_set_priority",
        "umc_v2_fake_vram", "umc_v2_shutdown",
    ],
    "allocator_v2.dll": [
        "umc_init", "umc_alloc", "umc_free", "umc_get_mem_info", "umc_reset",
        "umc_set_context", "umc_set_vram_limit", "umc_set_ram_limit",
        "umc_set_pagefile_limit", "umc_set_priority_mode",
    ],
    "umc_hook_v2.dll": [
        "umc_hook_install", "umc_hook_is_active", "umc_hook_uninstall",
        "umc_hooked_cudaMemGetInfo",
    ],
}


def probe(dll_dir=None):
    """Carrega cada .dll e confirma os exports. Devolve lista de linhas."""
    from umc_bridge_v3 import resolve_dll_dir

    dll_dir = resolve_dll_dir(dll_dir)
    if dll_dir is None:
        return ["DLLs: NAO ENCONTRADAS (use --dll-dir ou env UMC_V3_DLL_DIR)"]
    lines = ["directório: " + dll_dir]
    for name, exports in _EXPORTS.items():
        path = os.path.join(dll_dir, name)
        if not os.path.exists(path):
            lines.append("  {}: AUSENTE".format(name))
            continue
        try:
            lib = ctypes.CDLL(path)
        except OSError as e:
            lines.append("  {}: ERRO load ({})".format(name, e))
            continue
        missing = []
        for exp in exports:
            try:
                getattr(lib, exp)
            except AttributeError:
                missing.append(exp)
        if missing:
            lines.append("  {}: carregado - EM FALTA: {}".format(name, ", ".join(missing)))
        else:
            lines.append("  {}: carregado - {} exports ok".format(name, len(exports)))
    try:
        core = ctypes.CDLL(os.path.join(dll_dir, "VirtualGpuMemory.dll"))
        core.umc_v2_is_active.argtypes = []
        core.umc_v2_is_active.restype = ctypes.c_int
        lines.append("  umc_v2_is_active() neste processo: {} (0 = UMC so activo dentro do ComfyUI)".format(
            core.umc_v2_is_active()))
    except Exception as e:  # noqa: BLE001 - probe nunca pode rebentar
        lines.append("  umc_v2_is_active() falhou: {!r}".format(e))
    return lines


def _virtual_direct(dll_dir=None):
    """(total, free) virtual chamando a DLL fora do processo do ComfyUI."""
    from umc_bridge_v3 import resolve_dll_dir

    dll_dir = resolve_dll_dir(dll_dir)
    if dll_dir is None:
        return None, None
    try:
        core = ctypes.CDLL(os.path.join(dll_dir, "VirtualGpuMemory.dll"))
        core.umc_v2_is_active.argtypes = []
        core.umc_v2_is_active.restype = ctypes.c_int
        if not core.umc_v2_is_active():
            return None, None  # sem UMC activo aqui nao ha numeros virtuais
        core.umc_v2_mem_info.argtypes = [
            ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        core.umc_v2_mem_info.restype = None
        free_b = ctypes.c_size_t(0)
        total_b = ctypes.c_size_t(0)
        core.umc_v2_mem_info(ctypes.byref(free_b), ctypes.byref(total_b))
        return total_b.value, free_b.value
    except Exception:  # noqa: BLE001
        return None, None


def snapshot(dll_dir=None):
    """Mapa de memória do processo actual (dict)."""
    import umc_bridge_v3 as bridge

    bridge._init_physical()
    mv = bridge.mem_view()
    virt_t, virt_f = mv["virtual_total"], mv["virtual_free"]
    if virt_t is None:
        virt_t, virt_f = _virtual_direct(dll_dir)
    ram = swap = None
    try:
        import psutil
        ram = (psutil.virtual_memory().used, psutil.virtual_memory().total)
        swap = (psutil.swap_memory().used, psutil.swap_memory().total)
    except Exception:  # noqa: BLE001
        pass
    return {
        "ts": time.time(),
        "phys_total": mv["physical_total"],
        "phys_free": mv["physical_free"],
        "virt_total": virt_t,
        "virt_free": virt_f,
        "ram": ram,
        "swap": swap,
    }


def format_snapshot(d):
    lines = []
    lines.append("  física : livre {} / total {}".format(
        fmt_gb(d["phys_free"]), fmt_gb(d["phys_total"])))
    if d["virt_total"] is None:
        lines.append("  virtual: n/d (processo sem UMC activo)")
    else:
        lines.append("  virtual: livre {} / total {} (visão UMC)".format(
            fmt_gb(d["virt_free"]), fmt_gb(d["virt_total"])))
    if d["ram"]:
        lines.append("  RAM    : usado {} / total {}".format(fmt_gb(d["ram"][0]), fmt_gb(d["ram"][1])))
    if d["swap"]:
        lines.append("  pagefile: usado {} / total {}".format(fmt_gb(d["swap"][0]), fmt_gb(d["swap"][1])))
    return "\n".join(lines)


def watch(interval, dll_dir=None):
    """Amostra periódica -> logs/umc_v3_diag.csv."""
    path = os.path.join(log_dir(), "umc_v3_diag.csv")
    new_file = not os.path.exists(path)
    hdr = "ts,fisico_total,fisico_livre,virtual_total,virtual_livre,ram_usado,ram_total,pagefile_usado,pagefile_total\n"
    print("[UMC v3] watch {}s -> {}".format(interval, path))  # noqa: T201
    with open(path, "a", encoding="utf-8") as fh:
        if new_file:
            fh.write(hdr)
        while True:
            d = snapshot(dll_dir)
            ram = d["ram"] or (None, None)
            swap = d["swap"] or (None, None)
            fh.write("{:.0f},{},{},{},{},{},{},{},{}\n".format(
                d["ts"], d["phys_total"], d["phys_free"], d["virt_total"], d["virt_free"],
                ram[0], ram[1], swap[0], swap[1]))
            fh.flush()
            print("[{}] fisico_livre={} virtual_livre={}".format(  # noqa: T201
                time.strftime("%H:%M:%S"), fmt_gb(d["phys_free"]), fmt_gb(d["virt_free"])))
            time.sleep(interval)


def install_test_report(dll_dir=None):
    """Relatório do --install-test. Devolve True se os .dll estão todos ok."""
    print("[UMC v3] === install-test: probe de .dll ===")  # noqa: T201
    lines = probe(dll_dir)
    for ln in lines:
        print("  " + ln)  # noqa: T201
    dlls_ok = not any(s in " ".join(lines) for s in ("AUSENTE", "ERRO load", "EM FALTA", "NAO ENCONTRADAS"))
    print("[UMC v3] === install-test: estado de memória ===")  # noqa: T201
    print(format_snapshot(snapshot(dll_dir)))  # noqa: T201
    return dlls_ok


def main():
    ap = argparse.ArgumentParser(description="UMC v3 diag")
    ap.add_argument("--snapshot", action="store_true", help="imprimir estado (default)")
    ap.add_argument("--probe", action="store_true", help="verificar .dll/exports")
    ap.add_argument("--watch", type=float, default=0, metavar="SEG", help="amostrar CSV a cada N s")
    ap.add_argument("--dll-dir", type=str, default=None)
    a = ap.parse_args()
    if a.probe:
        for ln in probe(a.dll_dir):
            print(ln)  # noqa: T201
    elif a.watch > 0:
        watch(a.watch, a.dll_dir)
    else:
        print(format_snapshot(snapshot(a.dll_dir)))  # noqa: T201


if __name__ == "__main__":
    main()
