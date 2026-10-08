"""UMC v3 - bridge Python para o PyTorch.

Baseado no umc_bridge v2 (já testado em produção nesta máquina) com a evolução
central da "Fase 1": contabilidade dual-track.

  - Visão VIRTUAL (48GB etc.) continua a ser reportada em mem_get_info para o
    ComfyUI admitir/carregar modelos maiores que a VRAM física — NÃO se muda a
    semântica de admissão do v2 (qualquer mudança aqui altera a política de
    load do comfy.model_management e tem de ser decidida com dados).
  - Visão FÍSICA (pynvml) passa a estar disponível e exposta em
    physical_total/free/used() + nas chaves physical_bytes.* do memory_stats.
    É o sinal honesto que a Fase 3 (política de calor) vai consumir.

A camada nativa (allocator_v2.dll / umc_hook_v2.dll / VirtualGpuMemory.dll) é
reutilizada do UMC v2 — não há fonte nativa disponível; vê README.md.
"""
import ctypes
import os

from umc_common import fmt_gb, log

_installed = False
_CORE = None      # VirtualGpuMemory.dll
_PHYS = None      # (pynvml, handle) ou None


def _gb(n):
    return int(n * 1024 ** 3)


def _strip_async_backend_env():
    """Remove tokens 'backend:...' do PYTORCH allocator config.

    Igual ao v2: o backend:cudaMallocAsync conflita com o pluggable allocator
    (torch faz assert no mismatch quando o config é re-analisado em runtime,
    ex. dentro de torch.istft).
    """
    for var in ("PYTORCH_CUDA_ALLOC_CONF", "PYTORCH_ALLOC_CONF"):
        val = os.environ.get(var)
        if not val:
            continue
        kept = [t for t in val.split(",") if t.strip() and not t.strip().lower().startswith("backend:")]
        os.environ[var] = ",".join(kept)


def resolve_dll_dir(explicit=None):
    """Procura os .dll nativos do UMC.

    Ordem: dir explícito -> env UMC_V3_DLL_DIR -> instalação UMC v2 junto do
    ComfyUI (produção) -> cópias locais conhecidas. Devolve None se não achar.
    """
    names = ("allocator_v2.dll", "umc_hook_v2.dll", "VirtualGpuMemory.dll")
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("UMC_V3_DLL_DIR"):
        cands.append(os.environ["UMC_V3_DLL_DIR"])
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # UMC_v3_strata (ou _MEIPASS)
    cands += [
        os.path.join(os.getcwd(), "ComfyUI", "umc", "build"),
        os.path.join(base, "..", "ComfyUI", "umc", "build"),
        os.path.join(base, "build"),
        os.path.join(os.getcwd(), "build"),
    ]
    for c in cands:
        try:
            c = os.path.normpath(c)
        except OSError:
            continue
        if all(os.path.exists(os.path.join(c, n)) for n in names):
            return c
    return None


def _init_physical():
    """Arranca NVML para a visão física real (best-effort)."""
    global _PHYS
    if _PHYS is not None:
        return True
    try:
        import pynvml
        pynvml.nvmlInit()
        _PHYS = (pynvml, pynvml.nvmlDeviceGetHandleByIndex(0))
        return True
    except Exception as e:  # noqa: BLE001 - diagnóstico, nunca bloquear o arranque
        log("bridge", "pynvml indisponivel ({}); metrica fisica desativada".format(e))
        return False


def physical_total():
    if not _PHYS:
        return None
    try:
        return int(_PHYS[0].nvmlDeviceGetMemoryInfo(_PHYS[1]).total)
    except Exception:  # noqa: BLE001
        return None


def physical_free():
    if not _PHYS:
        return None
    try:
        return int(_PHYS[0].nvmlDeviceGetMemoryInfo(_PHYS[1]).free)
    except Exception:  # noqa: BLE001
        return None


def physical_used():
    t, f = physical_total(), physical_free()
    if t is None or f is None:
        return None
    return t - f


def virtual_view():
    """(livre, total) da visão virtual que o UMC apresenta ao torch."""
    if _CORE is None:
        return None, None
    free_b = ctypes.c_size_t(0)
    total_b = ctypes.c_size_t(0)
    _CORE.umc_v2_mem_info(ctypes.byref(free_b), ctypes.byref(total_b))
    return free_b.value, total_b.value


def mem_view():
    """Mapa unificado das duas contabilidades (para diag/placement)."""
    vfree, vtotal = virtual_view()
    return {
        "physical_total": physical_total(),
        "physical_free": physical_free(),
        "virtual_total": vtotal,
        "virtual_free": vfree,
    }


def install(dll_dir=None, vram_gb=48, ram_gb=48, pagefile_gb=40, priority="balanced"):
    """Instala o UMC v3: pluggable allocator + IAT hooks + mem_get_info fake.

    Tem de ser chamado ANTES de qualquer operação torch.cuda (mesma regra v2).
    """
    global _installed, _CORE
    if _installed:
        return True

    # Diz ao cuda_malloc.py do ComfyUI para não forçar backend:cudaMallocAsync.
    # Tem de ser antes do torch importar (mesma regra do v2).
    os.environ["UMC_V2"] = "1"
    _strip_async_backend_env()

    dll_dir = resolve_dll_dir(dll_dir)
    if dll_dir is None:
        log("bridge", "ERRO: DLLs nativas do UMC nao encontradas (use --dll-dir ou UMC_V3_DLL_DIR)")
        return False

    alloc_dll = os.path.join(dll_dir, "allocator_v2.dll")
    hook_dll = os.path.join(dll_dir, "umc_hook_v2.dll")
    core_dll = os.path.join(dll_dir, "VirtualGpuMemory.dll")
    log("bridge", "DLLs: {}".format(dll_dir))

    prio = {"balanced": 0, "max-performance": 1, "max-capacity": 2}.get(priority, 0)
    _init_physical()

    # 1. Pluggable allocator (antes de o CUDA ser tocado).
    import torch
    try:
        alloc = torch.cuda.memory.CUDAPluggableAllocator(alloc_dll, "umc_alloc", "umc_free")
        torch.cuda.memory.change_current_allocator(alloc)
    except Exception as e:  # noqa: BLE001 - degrada para o caminho v2+hooks
        log("bridge", "WARN: troca de pluggable allocator falhou: {}".format(e))

    # 2. IAT hooks (rede de segurança p/ chamadas CUDA fora do torch).
    hook = ctypes.CDLL(hook_dll)
    hook.umc_hook_install.argtypes = [ctypes.c_size_t] * 3 + [ctypes.c_int]
    hook.umc_hook_install.restype = ctypes.c_int
    rc = hook.umc_hook_install(_gb(vram_gb), _gb(ram_gb), _gb(pagefile_gb), prio)
    log("bridge", "IAT hooks: {}".format("sim" if rc == 1 else "nao (torch usa CUDA dinamico)"))

    # 3. Patch de mem_get_info -> visão virtual (mantém semântica v2 de admissão).
    _CORE = ctypes.CDLL(core_dll)
    _CORE.umc_v2_mem_info.argtypes = [ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    _CORE.umc_v2_mem_info.restype = None

    def fake_mem_get_info(device=None):
        return virtual_view()

    import torch.cuda.memory as cuda_memory
    cuda_memory.mem_get_info = fake_mem_get_info
    try:
        torch.cuda.mem_get_info = fake_mem_get_info
    except Exception:  # noqa: BLE001
        pass

    # 4. Shim de memory_stats (compat v2) + chaves physical_* honestas.
    _install_stats_shim(torch, cuda_memory)

    _installed = True
    mv = mem_view()
    log("bridge", "Virtual {} | Fisica {} (pynvml {}) | RAM {}GB | Pagefile {}GB | {}".format(
        fmt_gb(mv["virtual_total"]), fmt_gb(mv["physical_total"]),
        "ok" if _PHYS else "INDISPONIVEL", ram_gb, pagefile_gb, priority))
    return True


def _install_stats_shim(torch, cuda_memory):
    """memory_stats: semântica v2 (usado virtual) + entradas physical_* reais.

    Porque não substituímos tudo por números físicos já: o comfy.model_management
    lê 'reserved_bytes.all.current' em get_total_memory(); mudar aqui muda a
    política de admissão/unload do ComfyUI inteiro. É a Fase 3, com dados.
    """
    import collections

    # Lista completa do shim v2 (copiada verbatim do umc_bridge.py): qualquer
    # chave que o ComfyUI/nodes peçam a memory_stats() tem de existir, senão
    # KeyError em runtime. Nao encurtar.
    _STAT_KEYS = (
        "active.all.allocated", "active.all.current", "active.all.freed",
        "active.all.peak", "active.large_pool.allocated",
        "active.large_pool.current", "active.large_pool.freed",
        "active.large_pool.peak", "active.small_pool.allocated",
        "active.small_pool.current", "active.small_pool.freed",
        "active.small_pool.peak", "active_bytes.all.allocated",
        "active_bytes.all.current", "active_bytes.all.freed",
        "active_bytes.all.peak", "active_bytes.large_pool.allocated",
        "active_bytes.large_pool.current", "active_bytes.large_pool.freed",
        "active_bytes.large_pool.peak", "active_bytes.small_pool.allocated",
        "active_bytes.small_pool.current", "active_bytes.small_pool.freed",
        "active_bytes.small_pool.peak", "allocated_bytes.all.allocated",
        "allocated_bytes.all.current", "allocated_bytes.all.freed",
        "allocated_bytes.all.peak", "allocated_bytes.large_pool.allocated",
        "allocated_bytes.large_pool.current", "allocated_bytes.large_pool.freed",
        "allocated_bytes.large_pool.peak", "allocated_bytes.small_pool.allocated",
        "allocated_bytes.small_pool.current", "allocated_bytes.small_pool.freed",
        "allocated_bytes.small_pool.peak", "allocation.all.allocated",
        "allocation.all.current", "allocation.all.freed", "allocation.all.peak",
        "allocation.large_pool.allocated", "allocation.large_pool.current",
        "allocation.large_pool.freed", "allocation.large_pool.peak",
        "allocation.small_pool.allocated", "allocation.small_pool.current",
        "allocation.small_pool.freed", "allocation.small_pool.peak",
        "inactive_split.all.allocated", "inactive_split.all.current",
        "inactive_split.all.freed", "inactive_split.all.peak",
        "inactive_split.large_pool.allocated", "inactive_split.large_pool.current",
        "inactive_split.large_pool.freed", "inactive_split.large_pool.peak",
        "inactive_split.small_pool.allocated", "inactive_split.small_pool.current",
        "inactive_split.small_pool.freed", "inactive_split.small_pool.peak",
        "inactive_split_bytes.all.allocated", "inactive_split_bytes.all.current",
        "inactive_split_bytes.all.freed", "inactive_split_bytes.all.peak",
        "inactive_split_bytes.large_pool.allocated",
        "inactive_split_bytes.large_pool.current",
        "inactive_split_bytes.large_pool.freed",
        "inactive_split_bytes.large_pool.peak",
        "inactive_split_bytes.small_pool.allocated",
        "inactive_split_bytes.small_pool.current",
        "inactive_split_bytes.small_pool.freed",
        "inactive_split_bytes.small_pool.peak", "max_split_size",
        "num_alloc_retries", "num_device_alloc", "num_device_free", "num_ooms",
        "num_sync_all_streams", "oversize_allocations.allocated",
        "oversize_allocations.current", "oversize_allocations.freed",
        "oversize_allocations.peak", "oversize_segments.allocated",
        "oversize_segments.current", "oversize_segments.freed",
        "oversize_segments.peak", "requested_bytes.all.allocated",
        "requested_bytes.all.current", "requested_bytes.all.freed",
        "requested_bytes.all.peak", "requested_bytes.large_pool.allocated",
        "requested_bytes.large_pool.current", "requested_bytes.large_pool.freed",
        "requested_bytes.large_pool.peak",
        "requested_bytes.small_pool.allocated",
        "requested_bytes.small_pool.current",
        "requested_bytes.small_pool.freed",
        "requested_bytes.small_pool.peak", "reserved_bytes.all.allocated",
        "reserved_bytes.all.current", "reserved_bytes.all.freed",
        "reserved_bytes.all.peak", "reserved_bytes.large_pool.allocated",
        "reserved_bytes.large_pool.current", "reserved_bytes.large_pool.freed",
        "reserved_bytes.large_pool.peak", "reserved_bytes.small_pool.allocated",
        "reserved_bytes.small_pool.current", "reserved_bytes.small_pool.freed",
        "reserved_bytes.small_pool.peak", "segment.all.allocated",
        "segment.all.current", "segment.all.freed", "segment.all.peak",
        "segment.large_pool.allocated", "segment.large_pool.current",
        "segment.large_pool.freed", "segment.large_pool.peak",
        "segment.small_pool.allocated", "segment.small_pool.current",
        "segment.small_pool.freed", "segment.small_pool.peak",
        # extras honestos (Fase 1) - nao sao lidos pelo ComfyUI, so por nos/diag
        "physical_bytes.all.current", "physical_bytes.all.free", "physical_bytes.all.peak",
    )

    def _virtual_used():
        free_b, total_b = virtual_view()
        if not total_b:
            return 0
        return total_b - free_b

    def fake_memory_stats(device=None):
        used = _virtual_used()
        stats = collections.OrderedDict((k, 0) for k in _STAT_KEYS)
        for k in ("reserved_bytes.all.current", "reserved_bytes.all.peak",
                  "active_bytes.all.current", "allocated_bytes.all.current",
                  "requested_bytes.all.current", "segment.all.current"):
            stats[k] = used
        stats["active.all.current"] = used // 512
        stats["allocation.all.current"] = used // 512
        p_used = physical_used()
        if p_used is not None:
            stats["physical_bytes.all.current"] = p_used
            stats["physical_bytes.all.free"] = physical_free()
            stats["physical_bytes.all.peak"] = p_used
        return stats

    def fake_memory_allocated(device=None):
        return _virtual_used()

    fns = {
        "memory_stats": fake_memory_stats,
        "memory_allocated": fake_memory_allocated,
        "max_memory_allocated": fake_memory_allocated,
        "memory_reserved": fake_memory_allocated,
        "max_memory_reserved": fake_memory_allocated,
        "reset_peak_memory_stats": lambda device=None: None,
    }
    for name, fn in fns.items():
        setattr(torch.cuda, name, fn)
        setattr(cuda_memory, name, fn)


def verify():
    mv = mem_view()
    log("bridge", "mem_get_info(virtual) free={} total={} | NVML(fisica) free={} total={}".format(
        fmt_gb(mv["virtual_free"]), fmt_gb(mv["virtual_total"]),
        fmt_gb(mv["physical_free"]), fmt_gb(mv["physical_total"])))
    return mv
