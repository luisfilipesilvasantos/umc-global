"""UMC v3 - placement manager: técnicas Strata adaptadas (sem código Strata).

Dois hooks, ambos opcionais e nunca bloqueantes o arranque do ComfyUI:

1. comfy.model_management.load_models_gpu / free_memory
   Regista cada carregamento/descarga (nº de modelos, tamanho, duração).
   É a baseline de dados da Fase 0 — sem isto não há política informada.

2. execution.execute (por nó)
   Prefetch preditivo: o ComfyUI conhece o grafo completo antes de executar,
   logo a previsão é exacta (não heurística). Antes de cada nó andamos para
   trás pelas inputs até aos outputs já materializados e recolhemos os
   ModelPatcher (UNet/CLIP/VAE) que ainda não estão carregados; carregamo-los
   síncronamente na fronteira do nó, escondendo a latência atrás do nó actual.
   NOTA: tem de ser síncrono — o event loop não cicla durante a execução de um
   workflow (tasks agendadas só correm depois de "Prompt executed"; verificado
   em teste), logo um prefetch asyncio nunca correria a tempo.

Segurança:
- Kill-switch: UMC_V3_PREFETCH=0 ou --no-prefetch.
- Guarda de margem: só prefetch se a memória virtual livre couber o necessário
  + 2GB; caso contrário deixa o load reactivo decidir (evita descarregar o
  modelo activo a meio de um nó).
- Tudo em try/except: erro de prefetch mata só o prefetch, nunca o workflow.

Fase 3 (política de calor + integridade):
- A cada prompt novo (mudança de prompt_id, mesmo com prefetch desligado):
  epoch do calor (umc_heat), verificação do canário de integridade
  (umc_integrity) e aviso LOW de VRAM física (edge-trigger, < 512 MiB).
- Prefetch carrega os modelos por calor decrescente (os mais quentes primeiro).
- Evicção guardada (umc_heat.MARGEM/heat_of): antes de free_memory actuar,
  se a VRAM virtual estiver apertada (< 4 GiB livres) descarrega os modelos
  FRIOS primeiro (calor 0, ou calor + MARGEM <= o mais quente), máx. 3 por
  chamada com cooldown de 30 s — o free_memory do ComfyUI vê espaço e não
  precisa de descarregar os quentes. Nunca toca em: for_dynamic,
  keep_loaded, modelos recém-carregados (protected) nem se o utilizador
  desligou a smart memory. Kill-switch: UMC_V3_HEAT_POLICY=0.
"""
import os
import sys
import time

import umc_heat
import umc_integrity
from umc_common import fmt_gb, log

_MARGEM = 2 * 1024 ** 3
_LOW_PHYS = 512 * 1024 ** 2          # aviso LOW de VRAM física livre
_EVICTION_FREE = 4 * 1024 ** 3       # só eviciona com < 4 GiB virtuais livres
_EVICTION_TARGET_EXTRA = 2 * 1024 ** 3   # alvo: pedido + 2 GiB (min. 4 GiB)
_EVICTION_MAX = 3                    # máx. descarregamentos por free_memory
_EVICTION_COOLDOWN_S = 30.0

_heat_on = os.environ.get("UMC_V3_HEAT_POLICY", "1") != "0"
_prefetch_on = True
_installed = False
_deferred = False
_skip_seen = None
_orig_execute = None
_protected = set()       # ids dos patchers carregados recentemente (anti-evicção)
_last_eviction = 0.0
_last_prompt = None
_low_phys = False        # edge-trigger do aviso LOW
_hooks = {"load": False, "free": False, "execute": False}
_stats = {"loads": 0, "frees": 0, "prefetch_ok": 0, "prefetch_skip": 0, "prefetch_err": 0,
          "evictions": 0}


def _patcher_of(obj):
    """Devolve o ModelPatcher associado a uma output (ou None)."""
    # Import adiado: comfy.model_patcher importa comfy_aimdo.model_vbar e nao
    # pode ser carregado antes do init do aimdo (ver docstring do modulo).
    from comfy.model_patcher import ModelPatcher, is_model_patcher_output
    if isinstance(obj, ModelPatcher):
        return obj
    if is_model_patcher_output(obj):
        return obj.patcher
    return None


def _outputs_of(caches, node_id):
    """Valores das outputs já materializados de um nó (achatados) ou None."""
    try:
        local = caches.outputs.get_local(node_id)
    except Exception:  # noqa: BLE001
        return None
    if local is None:
        return None
    outs = getattr(local, "outputs", None)
    if outs is None and isinstance(local, (list, tuple)):
        outs = local
    if not isinstance(outs, (list, tuple)):
        return None
    # CacheEntry.outputs e uma lista por slot ([[v],[v],...]) — merge_result_data;
    # achatar ate aos valores, mesmo padrao de PromptModelTracker.add.
    flat = []
    stack = list(outs)
    while stack:
        v = stack.pop()
        if isinstance(v, (list, tuple)):
            stack.extend(v)
        else:
            flat.append(v)
    return flat


def _collect_patchers(dynprompt, caches, roots):
    """Anda para trás no grafo a partir de roots e recolhe ModelPatchers
    já materializados (o nó pendente ainda não tem outputs; os loaders sim)."""
    found = []
    seen = set()
    stack = [n for n in roots if n]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        try:
            node = dynprompt.get_node(nid)
        except Exception:  # noqa: BLE001 - nó efémero/removido
            continue
        if not isinstance(node, dict):
            continue
        for v in (node.get("inputs") or {}).values():
            # link de prompt: ["id_do_nó", slot]
            if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str):
                stack.append(v[0])
        for out in (_outputs_of(caches, nid) or ()):
            p = _patcher_of(out)
            if p is not None:
                found.append(p)
    return found


def _free_ok(needed):
    """Margem de segurança: com a visão virtual, free é o que o UMC admite.
    Sem certeza (exceção), não arriscar prefetch — o load reactivo cobre."""
    try:
        import comfy.model_management as mm
        dev = mm.get_torch_device()
        if getattr(dev, "type", None) == "cpu":
            return True
        return mm.get_free_memory(dev) >= needed + _MARGEM
    except Exception:  # noqa: BLE001
        return False


def _skip_note(prompt_id, key, msg):
    """Nota de skip só uma vez por prompt (senão seriam 7 linhas por workflow)."""
    global _skip_seen
    if _skip_seen == (prompt_id, key):
        return
    _skip_seen = (prompt_id, key)
    log("place", "prefetch SKIP prompt={} ({})".format(prompt_id, msg))


def _prefetch_now(dynprompt, caches, execution_list, current_item, prompt_id):
    """Prefetch síncrono chamado na fronteira de cada nó (ver docstring)."""
    try:
        roots = set(getattr(execution_list, "pendingNodes", {}) or {})
        if current_item is not None:
            roots.add(current_item)
        if not roots:
            return
        patchers = _collect_patchers(dynprompt, caches, roots)
        if not patchers:
            _stats["prefetch_skip"] += 1
            _skip_note(prompt_id, "vazio", "0 patchers materializados")
            return
        import comfy.model_management as mm
        loaded = {id(lm.model) for lm in mm.current_loaded_models}
        todo = []
        seen = set()
        for p in patchers:
            if id(p) in loaded or id(p) in seen:
                continue
            seen.add(id(p))
            todo.append(p)
        if not todo:
            _stats["prefetch_skip"] += 1
            _skip_note(prompt_id, "carregado", "modelos ja carregados")
            return
        if _heat_on:
            # mais quentes primeiro: se faltar espaço a meio do lote, os
            # modelos que o grafo mais usa já estão carregados
            todo.sort(key=umc_heat.heat_of, reverse=True)
        needed = sum(p.model_size() for p in todo)
        if not _free_ok(needed):
            _stats["prefetch_skip"] += 1
            log("place", "prefetch SKIP prompt={} (mem insuficiente p/ {})".format(
                prompt_id, fmt_gb(needed)))
            return
        t0 = time.perf_counter()
        mm.load_models_gpu(todo)
        dt = int((time.perf_counter() - t0) * 1000)
        _stats["prefetch_ok"] += 1
        names = ", ".join(type(getattr(p, "model", p)).__name__ for p in todo)
        log("place", "prefetch prompt={} n={} size={} {}ms [{}]".format(
            prompt_id, len(todo), fmt_gb(needed), dt, names))
    except Exception as e:  # noqa: BLE001 - nunca propagar para a execução
        _stats["prefetch_err"] += 1
        log("place", "prefetch ERRO: {!r}".format(e))


def _on_new_prompt(prompt_id):
    """Mudança de prompt: epoch do calor, integridade e aviso LOW físico.

    Corre mesmo com o prefetch desligado — é a tique do relógio da Fase 3.
    """
    global _last_prompt, _low_phys
    _last_prompt = prompt_id
    if _heat_on:
        try:
            umc_heat.epoch()
        except Exception as e:  # noqa: BLE001
            log("place", "epoch ERRO: {!r}".format(e))
    try:
        umc_integrity.verify_epoch(prompt_id)
    except Exception as e:  # noqa: BLE001
        log("place", "integrity ERRO: {!r}".format(e))
    try:
        import umc_bridge_v3
        pf = umc_bridge_v3.physical_free()
        if pf is not None:
            low = pf < _LOW_PHYS
            if low and not _low_phys:
                log("place", "LOW: so {} MiB de VRAM fisicos livres".format(pf // 1024 ** 2))
            elif not low and _low_phys:
                log("place", "VRAM fisica recuperada: {} MiB livres".format(pf // 1024 ** 2))
            _low_phys = low
    except Exception:  # noqa: BLE001
        pass


async def _hooked_execute(*args, **kwargs):
    prompt_id = args[6] if len(args) > 6 else kwargs.get("prompt_id")
    if prompt_id is not None and prompt_id != _last_prompt:
        try:
            _on_new_prompt(prompt_id)
        except Exception as e:  # noqa: BLE001 - a tique nunca parte a execução
            log("place", "on_new_prompt ERRO: {!r}".format(e))
    if _prefetch_on:
        try:
            # Assinatura posicional de execution.execute (v0.34):
            # server, dynprompt, caches, current_item, extra_data, executed,
            # prompt_id, execution_list, pending_subgraph_results,
            # pending_async_nodes, ui_outputs
            dynprompt = args[1] if len(args) > 1 else kwargs.get("dynprompt")
            caches = args[2] if len(args) > 2 else kwargs.get("caches")
            current_item = args[3] if len(args) > 3 else kwargs.get("current_item")
            execution_list = args[7] if len(args) > 7 else kwargs.get("execution_list")
            if None not in (dynprompt, caches, execution_list):
                _prefetch_now(dynprompt, caches, execution_list, current_item, prompt_id)
        except Exception as e:  # noqa: BLE001
            _stats["prefetch_err"] += 1
            log("place", "prefetch falhou: {!r}".format(e))
    return await _orig_execute(*args, **kwargs)


def _wrap_load(mm):
    orig = mm.load_models_gpu

    def load_wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        r = orig(*args, **kwargs)
        dt = int((time.perf_counter() - t0) * 1000)
        _stats["loads"] += 1
        try:
            models = args[0] if args else kwargs.get("models") or []
            size = sum(m.model_size() for m in models)
            names = ", ".join(type(getattr(m, "model", m)).__name__ for m in models)
        except Exception:  # noqa: BLE001
            size, names = -1, "?"
        mem_req = args[1] if len(args) > 1 else kwargs.get("memory_required", 0)
        log("place", "load n={} req={} size={} {}ms [{}]".format(
            len(models), fmt_gb(mem_req) if isinstance(mem_req, (int, float)) else mem_req,
            fmt_gb(size) if size >= 0 else "?", dt, names))
        if _heat_on:
            try:
                for m in models:
                    umc_heat.note_use(m)   # carregado == usado (calor)
                # lote recém-carregado fica protegido da evicção (anti-thrash:
                # nunca descarregar logo o que acabou de entrar)
                _protected.clear()
                _protected.update(id(m) for m in models)
            except Exception:  # noqa: BLE001 - calor nunca parte o load
                pass
        return r

    mm.load_models_gpu = load_wrapper
    _hooks["load"] = True


def _evict_if_needed(args, kwargs):
    """Evicção guardada por calor sob pressão virtual (ver docstring do módulo).

    Corre ANTES de free_memory actuar: se a visão virtual estiver apertada,
    descarrega os modelos frios primeiro, para que o free_memory do ComfyUI
    (que descalça por offloaded/refcount/tamanho, sem calor) não precise de
    tocar nos quentes. Nunca propaga excepções.
    """
    global _last_eviction
    if not _heat_on:
        return
    try:
        import comfy.model_management as mm
    except Exception:  # noqa: BLE001
        return
    if getattr(mm, "DISABLE_SMART_MEMORY", False):
        return   # utilizador pediu comportamento simples: não nos metemos
    keep = args[2] if len(args) > 2 else kwargs.get("keep_loaded", [])
    for_dynamic = args[3] if len(args) > 3 else kwargs.get("for_dynamic", False)
    if for_dynamic:
        return   # unload dinâmico: o chamador tem planos próprios
    if time.time() - _last_eviction < _EVICTION_COOLDOWN_S:
        return
    try:
        import umc_bridge_v3
        vfree, _vtotal = umc_bridge_v3.virtual_view()
    except Exception:  # noqa: BLE001
        return
    if vfree is None or vfree >= _EVICTION_FREE:
        return   # sem pressão virtual não se mexe nada
    mem_req = args[0] if args else kwargs.get("memory_required", 0)
    if not isinstance(mem_req, (int, float)) or mem_req >= 1e30:
        mem_req = 0
    device = args[1] if len(args) > 1 else kwargs.get("device")
    goal = max(_EVICTION_FREE, mem_req + _EVICTION_TARGET_EXTRA)
    # candidatos: carregados, vivos, não usados NESTA ronda (currently_used),
    # não protegidos, não no keep_loaded e no mesmo device — mesmas regras do
    # free_memory, mais as do UMC (protected/heat)
    cands = []
    for i, lm in enumerate(mm.current_loaded_models):
        p = lm.model
        if p is None or lm.is_dead() or lm.currently_used:
            continue
        if device is not None and lm.device != device:
            continue
        if id(p) in _protected or (keep and lm in keep):
            continue
        cands.append((umc_heat.heat_of(p), -lm.model_offloaded_memory(),
                      sys.getrefcount(p), lm.model_memory(), i, lm))
    if not cands:
        return
    hottest = max(c[0] for c in cands)
    victims = [c for c in cands if c[0] == 0.0 or c[0] + umc_heat.MARGEM <= hottest]
    victims.sort(key=lambda c: c[:5])   # (calor, -offloaded, refcount, tamanho, i)
    evicted = []
    freed = 0
    for h, _off, _ref, mem, i, lm in victims:
        if len(evicted) >= _EVICTION_MAX or vfree + freed >= goal:
            break
        freed += lm.model_loaded_memory()
        if lm.model_unload(1e32):   # 1e32 > qualquer tamanho -> descarga completa
            evicted.append((i, lm, h))
    for i, _lm, _h in sorted(evicted, reverse=True):
        mm.current_loaded_models.pop(i)
    if not evicted:
        return
    _last_eviction = time.time()
    _stats["evictions"] += len(evicted)
    mm.soft_empty_cache()
    log("place", "eviction: {} modelos ({} a frio) | virt livre {} -> alvo {} | "
        "saiem: {}".format(
            len(evicted), sum(1 for _i, _l, h in evicted if h == 0.0),
            fmt_gb(vfree), fmt_gb(goal),
            ", ".join("{}={:.1f}".format(
                type(getattr(lm.model, "model", lm.model)).__name__, h)
                for _i, lm, h in evicted)))


def _wrap_free(mm):
    orig = mm.free_memory

    def free_wrapper(*args, **kwargs):
        try:
            _evict_if_needed(args, kwargs)
        except Exception as e:  # noqa: BLE001 - evicção nunca parte o free
            log("place", "eviction ERRO: {!r}".format(e))
        r = orig(*args, **kwargs)
        _stats["frees"] += 1
        try:
            req = args[0] if args else kwargs.get("memory_required", 0)
            n = len(r) if r is not None else 0
        except Exception:  # noqa: BLE001
            req, n = "?", 0
        req_s = fmt_gb(req) if isinstance(req, (int, float)) and req < 1e30 else "inf"
        log("place", "free req={} descarregados={}".format(req_s, n))
        return r

    mm.free_memory = free_wrapper
    _hooks["free"] = True


def _do_install():
    """Instala os hooks de facto. Idempotente; nunca propaga exceções.

    AVISO: importar comfy.model_management/execution aqui tem de acontecer
    DEPOIS de comfy_aimdo.control.init() — os módulos comfy_aimdo
    (host_buffer/model_vbar/...) fazem snapshot de control.lib NO IMPORT;
    importar cedo crava lib=None e rebenta em ModelPatcher/__finally__ do
    execute (visto em teste real: 'NoneType' hostbuf_allocate).
    """
    global _installed, _orig_execute
    if _installed:
        return True
    try:
        import comfy.model_management as mm
        _wrap_load(mm)
        _wrap_free(mm)
        log("place", "hooks de contabilidade instalados (load_models_gpu/free_memory)")
    except Exception as e:  # noqa: BLE001
        log("place", "hooks de contabilidade FALHARAM: {!r}".format(e))
    try:
        import execution
        _orig_execute = execution.execute
        execution.execute = _hooked_execute
        _hooks["execute"] = True
        log("place", "hook execution.execute instalado (prefetch={})".format(
            "ligado" if _prefetch_on else "desligado"))
    except Exception as e:  # noqa: BLE001 - sem prefetch ComfyUI arranca na mesma
        log("place", "hook execution.execute FALHOU (sem prefetch): {!r}".format(e))
    _installed = True
    return True


def _aimdo_will_init():
    """O main.py vai chamar comfy_aimdo.control.init/init_devices?

    Espelhe as condições de main.py:68-80 (init) e main.py:275
    (init_devices). Em dúvida -> True (esperar pelo trigger em vez de
    importar cedo).
    """
    try:
        from comfy.cli_args import args as _cli_args
        from comfy.cli_args import enables_dynamic_vram
        return bool(getattr(_cli_args, "enable_dynamic_vram", False)) or bool(
            enables_dynamic_vram())
    except Exception:  # noqa: BLE001
        return True


def _defer_install_until_aimdo():
    """Adia _do_install até comfy_aimdo.control.init/init_devices ser chamado
    pelo main.py (qualquer um dos dois dispara; o 1º é o init, main.py:73,
    que carrega o control.lib de que os snapshots dependem).

    Devolve False se não há comfy_aimdo para embrulhar (nesse caso importar
    cedo é inofensivo: o aimdo nem fica activo).
    """
    global _deferred
    try:
        import comfy_aimdo.control as ctrl
    except Exception:  # noqa: BLE001 - sem aimdo -> instalar directamente
        return False

    def _wrap(name):
        orig = getattr(ctrl, name, None)
        if not callable(orig):
            return

        def wrapper(*a, **kw):
            r = orig(*a, **kw)  # exceções (ex. TypeError de protocolo) propagam
            if not _installed:
                log("place", "aimdo.{} chamado -> instalar hooks agora".format(name))
                _do_install()
            return r

        setattr(ctrl, name, wrapper)

    _wrap("init")
    _wrap("init_devices")
    _deferred = True
    return True


def install(prefetch=True):
    """Prepara os hooks. Nunca levanta exceção para o chamador.

    Os imports de comfy.* são adiados até comfy_aimdo.control.init()
    (ver _do_install); só instala já se o aimdo não for inicializar.
    """
    global _prefetch_on, _deferred
    if _installed or _deferred:
        return True
    _prefetch_on = prefetch and os.environ.get("UMC_V3_PREFETCH", "1") != "0"
    if _defer_install_until_aimdo() and _aimdo_will_init():
        log("place", "hooks adiados ate comfy_aimdo.control.init/init_devices do main.py")
        return True
    # Sem aimdo no processo (ou desactivado): importar agora e seguro.
    _deferred = False
    return _do_install()


def fire_aimdo_init_for_test():
    """--install-test: chama comfy_aimdo.control.init como o main.py faria
    (main.py:68-80), para disparar os hooks adiados e validar o caminho
    real. Só corre no teste; em produção quem chama é o próprio main.py.
    Devolve True se o trigger foi exercitado.
    """
    if _installed and not _deferred:
        return True  # instalação directa (sem aimdo) já feita
    try:
        from comfy.cli_args import args as _a
        import comfy_aimdo.control as ctrl
        if not _aimdo_will_init():
            return False  # aimdo off -> _do_install já correu em install()
        headroom = (None if getattr(_a, "reserve_vram", None) is None
                    else int(_a.reserve_vram * 1024 ** 3))
        try:
            ctrl.init(simple_vram_headroom=headroom,
                      nvml_pressure=not getattr(_a, "disable_nvml_pressure", False))
        except TypeError:
            # protocolos comfy-aimdo 0.4.10 / 0.4.9 (mesma dança do main.py)
            try:
                ctrl.init(simple_vram_headroom=headroom)
            except TypeError:
                ctrl.init()
        return True
    except Exception as e:  # noqa: BLE001 - sinalizado no relatório
        log("place", "install-test: trigger aimdo falhou: {!r}".format(e))
        return False


def summary():
    return "hooks={} adiado={} stats={}".format(
        {k: v for k, v in _hooks.items()}, _deferred and not _installed, _stats)
