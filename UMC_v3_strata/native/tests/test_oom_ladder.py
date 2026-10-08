#!/usr/bin/env python
"""Fase 1 — testes de aceitação do allocator (exit != 0 em falha).

Carga o allocator_v2.dll (build/Release) e testa:
1. Contrato de producao (dual exports): umc_alloc/umc_free/umc_hook_install
2. Telemetria NVML: total > 0, free > 0, pressao em [0,1]
3. vista virtual: apos umc_set_virtual_budget(48GB), total=48GB, free=48GB-tracked
4. OOM gracioso: umc_alloc(100 GiB) -> None (sem crash); alloc pequeno depois funciona
5. Ladder: umc_alloc_with_retry com fn sempre-OOM -> None (sem crash, sem engolir)

Exit 0 = todos OK; exit 1 = alguma falha (regra: testes que falham de verdade).
"""
import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.path.join(HERE, "..", "build", "Release", "allocator_v2.dll")

GiB = 1024 ** 3

failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "FALHA"
    print("[TESTE] {:<28} {} {}".format(name, status, detail))
    if not cond:
        failures.append(name)


def main():
    if not os.path.isfile(DLL_PATH):
        print("[TESTE] ERRO: nao encontrei {}".format(DLL_PATH))
        return 1
    dll = ctypes.CDLL(DLL_PATH)

    # --- 1. Contrato de producao (dual exports) ---
    for sym in ("umc_alloc", "umc_free", "umc_init_allocator", "umc_cleanup_allocator",
                "umc_alloc_with_retry", "umc_nvml_physical_free", "umc_nvml_physical_total",
                "umc_physical_pressure_ratio", "umc_set_virtual_budget",
                "umc_get_virtual_total", "umc_get_virtual_view"):
        check("export:{}".format(sym), hasattr(dll, sym))

    # Assinaturas
    dll.umc_nvml_physical_free.restype = ctypes.c_size_t
    dll.umc_nvml_physical_total.restype = ctypes.c_size_t
    dll.umc_physical_pressure_ratio.restype = ctypes.c_double
    dll.umc_set_virtual_budget.argtypes = [ctypes.c_size_t] * 3
    dll.umc_get_virtual_total.restype = ctypes.c_size_t
    dll.umc_get_virtual_view.argtypes = [ctypes.POINTER(ctypes.c_size_t)] * 2
    dll.umc_get_virtual_view.restype = ctypes.c_int
    dll.umc_track_alloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    dll.umc_track_free.argtypes = [ctypes.c_void_p]
    dll.umc_get_tracked_total.restype = ctypes.c_size_t
    dll.umc_alloc.argtypes = [ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p]
    dll.umc_alloc.restype = ctypes.c_void_p
    dll.umc_free.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p]

    class RetryConfig(ctypes.Structure):
        _fields_ = [("max_attempts", ctypes.c_int),
                    ("backoff_ms_base", ctypes.c_int),
                    ("nvml_log_every", ctypes.c_int)]
    dll.umc_alloc_with_retry.argtypes = [
        ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.POINTER(ctypes.c_void_p)),
        ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(RetryConfig),
        ctypes.POINTER(ctypes.c_int)]
    dll.umc_alloc_with_retry.restype = ctypes.c_void_p

    # --- 2. Telemetria NVML ---
    total = dll.umc_nvml_physical_total()
    free = dll.umc_nvml_physical_free()
    press = dll.umc_physical_pressure_ratio()
    check("nvml:total>0", total > 0, "total={}".format(total))
    check("nvml:free>0", free > 0, "free={}".format(free))
    check("nvml:pressao", 0.0 <= press <= 1.0, "pressao={:.3f}".format(press))

    # --- 3. Vista virtual ---
    dll.umc_set_virtual_budget(48 * GiB, 48 * GiB, 40 * GiB)
    vfree, vtotal = ctypes.c_size_t(0), ctypes.c_size_t(0)
    rc = dll.umc_get_virtual_view(ctypes.byref(vfree), ctypes.byref(vtotal))
    check("vista:rc", rc == 1, "rc={}".format(rc))
    check("vista:total=48GiB", vtotal.value == 48 * GiB, "total={}".format(vtotal.value))
    check("vista:free=48GiB-tracked",
          vfree.value == 48 * GiB - dll.umc_get_tracked_total(),
          "free={} tracked={}".format(vfree.value, dll.umc_get_tracked_total()))

    # --- 4. OOM gracioso + estado intacto ---
    big = dll.umc_alloc(100 * GiB, 0, None)
    check("oom:100GiB->None", not big, "ptr={}".format(big))
    small = dll.umc_alloc(1024 * 1024, 0, None)  # 1 MiB
    check("oom:1MiB depois", bool(small), "ptr={}".format(small))
    if small:
        dll.umc_free(small, 1024 * 1024, 0, None)

    # --- 5. Ladder com fn sempre-OOM (retryable) ---
    FAIL_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
                               ctypes.POINTER(ctypes.c_void_p))

    def always_oom(ctx, size, out):
        return 1  # OOM retryable

    cb = FAIL_CB(always_oom)
    cfg = RetryConfig(max_attempts=3, backoff_ms_base=1, nvml_log_every=0)
    err = ctypes.c_int(-1)
    res = dll.umc_alloc_with_retry(cb, None, 1024, ctypes.byref(cfg), ctypes.byref(err))
    check("ladder:OOM->None", not res, "res={}".format(res))
    check("ladder:out_err=1", err.value == 1, "out_err={}".format(err.value))

    # --- 6. Ladder com erro real (nao engolir) ---
    def real_error(ctx, size, out):
        return 2  # erro real: devolver imediato

    cb2 = FAIL_CB(real_error)
    err2 = ctypes.c_int(-1)
    res2 = dll.umc_alloc_with_retry(cb2, None, 1024, ctypes.byref(cfg), ctypes.byref(err2))
    check("ladder:erro real->None", not res2, "res={}".format(res2))
    check("ladder:out_err=2", err2.value == 2, "out_err={}".format(err2.value))

    print("[TESTE] {}".format("TODOS OK" if not failures else "FALHAS: {}".format(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
