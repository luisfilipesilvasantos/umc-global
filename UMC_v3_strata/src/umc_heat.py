"""UMC v3 - Fase 3: calor online por modelo + perfil persistido.

Técnicas Strata adaptadas (sem código Strata):

 - Decay exponencial por epoch (adapt_every): heat += usage; usage *= DECAY a
   cada prompt submetido — heat fica a soma das contagens já decaídas, uma
   integral exponencialmente ponderada do uso recente.
 - Perfil persistido entre sessões (expert_profile_save): escrita atómica
   (.tmp + os.replace), sha256 interno das entradas (detecta corrupção
   acidental, não adulteração) e cópia .bak da geração anterior; um ficheiro
   ilegível/corrompido cai no .bak em vez de perder o histórico.
 - Histerese: largar um candidato frio só se o mais quente o superar em
   MARGEM — evita thrashing entre modelos de popularidade parecida.

Identidade: chave "<classe_interna>:<bytes>" do ModelPatcher — identidade de
trabalho, não de ficheiro (dois checkpoints da mesma arquitectura e tamanho
partilham calor; aceitável: partilham também o custo de carregamento).
"""
import atexit
import hashlib
import json
import os
import shutil
import threading
import time
import weakref

from umc_common import log, log_dir

PROFILE_NAME = "umc_heat_profile.json"
PROFILE_VERSION = 1
DECAY = 0.7          # por epoch (prompt submetido)
MARGEM = 1.5         # histerese: quente tem de superar o frio por isto
MAX_ENTRIES = 64
SAVE_INTERVAL_S = 120.0

_lock = threading.Lock()
_entries = {}        # key -> {"heat", "usage", "size", "last"}
_keys = weakref.WeakKeyDictionary()   # model -> key (evita model_size repetido)
_loaded = False
_dirty = False
_last_save = 0.0
_peak_hot_bytes = 0


def profile_path():
    return os.path.join(log_dir(), PROFILE_NAME)


def _entries_sha(entries):
    """sha256 canónico das entradas — detecção de corrupção acidental."""
    blob = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def model_key(model):
    """Chave estável do modelo: classe interna + tamanho (memo por objeto)."""
    k = _keys.get(model)
    if k is None:
        try:
            size = model.model_size()
        except Exception:  # noqa: BLE001 - identidade incompleta é melhor que nada
            size = -1
        k = "{}:{}".format(type(getattr(model, "model", model)).__name__, size)
        try:
            _keys[model] = k
        except TypeError:  # nao hashable - segue sem memo
            pass
    return k


def note_use(model):
    """Regista um uso (load / prefetch / necessário no grafo) do modelo."""
    global _dirty
    _ensure_loaded()
    try:
        key = model_key(model)
        size = int(key.rsplit(":", 1)[1])
    except Exception:  # noqa: BLE001
        return
    with _lock:
        e = _entries.get(key)
        if e is None:
            if len(_entries) >= MAX_ENTRIES:
                drop = min(_entries, key=lambda k: _entries[k]["heat"])
                _entries.pop(drop, None)   # o mais frio sai primeiro
            e = _entries[key] = {"heat": 0.0, "usage": 0.0, "size": size,
                                 "last": time.time()}
        e["usage"] += 1.0
        e["last"] = time.time()
        _dirty = True


def heat_of(model):
    """Calor acumulado do modelo (0.0 se desconhecido)."""
    _ensure_loaded()
    try:
        key = model_key(model)
    except Exception:  # noqa: BLE001
        return 0.0
    with _lock:
        e = _entries.get(key)
        return e["heat"] if e else 0.0


def epoch():
    """Prompt submetido: heat += usage; usage *= DECAY; resumo + guarda."""
    global _dirty
    _ensure_loaded()
    with _lock:
        for e in _entries.values():
            e["heat"] += e["usage"]
            e["usage"] *= DECAY
        top = sorted(_entries.items(), key=lambda kv: -kv[1]["heat"])[:3]
        n = len(_entries)
        _dirty = True
    if n:
        log("heat", "epoch: {} modelos | top: {}".format(
            n, ", ".join("{}={:.1f}".format(k.split(":")[0], v["heat"])
                         for k, v in top)))
    save_profile()


def totals():
    """(bytes com calor>0, nº com calor>0, peak) — chaves heat_bytes.*."""
    global _peak_hot_bytes
    _ensure_loaded()
    with _lock:
        hot = [e for e in _entries.values() if e["heat"] > 0]
        b = sum(e["size"] for e in hot)
    if b > _peak_hot_bytes:
        _peak_hot_bytes = b
    return b, len(hot), _peak_hot_bytes


def summary():
    _ensure_loaded()
    with _lock:
        return "heat={}/{} modelos".format(
            sum(1 for e in _entries.values() if e["heat"] > 0), len(_entries))


def save_profile(force=False):
    """Escrita atómica (.tmp + os.replace) — falha nunca parte o ficheiro bom."""
    global _dirty, _last_save
    with _lock:
        if not _dirty:
            return
        now = time.time()
        if not force and now - _last_save < SAVE_INTERVAL_S:
            return
        entries_out = {k: {"heat": round(e["heat"], 3),
                           "usage": round(e["usage"], 3),
                           "size": e["size"], "last": int(e["last"])}
                       for k, e in _entries.items()}
        payload = {
            "version": PROFILE_VERSION,
            "saved_at": int(now),
            "sha256": _entries_sha(entries_out),
            "entries": entries_out,
        }
        path = profile_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            # a geração anterior boa passa a .bak (fallback de leitura)
            if os.path.isfile(path):
                shutil.copyfile(path, path + ".bak")
            os.replace(tmp, path)
            _dirty = False
            _last_save = now
        except OSError as ex:  # o perfil antigo fica intacto
            log("heat", "perfil NAO guardado: {!r}".format(ex))


def _valid_payload(path):
    """Lê e valida um ficheiro de perfil; None se ilegível/corrompido."""
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as ex:
        log("heat", "perfil ignorado (ilegivel) {}: {!r}".format(path, ex))
        return None
    if (not isinstance(payload, dict)
            or payload.get("version") != PROFILE_VERSION
            or not isinstance(payload.get("entries"), dict)):
        log("heat", "perfil ignorado (versao/geometria errada) em {}".format(path))
        return None
    want = payload.get("sha256")   # ausente em ficheiros antigos: aceita-se
    if want and want != _entries_sha(payload["entries"]):
        log("heat", "perfil corrompido (sha256 divergente) em {}".format(path))
        return None
    return payload


def _ensure_loaded():
    """Carrega o perfil uma vez; principal -> .bak; recusa geometria errada."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = profile_path()
    payload = _valid_payload(path)
    if payload is None:
        payload = _valid_payload(path + ".bak")
        if payload is None:
            return   # primeira sessão (ou nada legível): segue sem histórico
        log("heat", "perfil principal invalido; recuperado de .bak")
    now = time.time()
    ok = 0
    with _lock:
        for k, v in payload["entries"].items():
            if (not isinstance(k, str) or not isinstance(v, dict)
                    or not isinstance(v.get("heat"), (int, float))):
                continue   # entrada corrompida: recusa só essa entrada
            _entries[k] = {"heat": float(v["heat"]),
                           "usage": float(v.get("usage") or 0.0),
                           "size": int(v.get("size") or 0),
                           "last": float(v.get("last") or now)}
            ok += 1
    if ok:
        log("heat", "perfil carregado: {} modelos quentes de {}".format(ok, path))


atexit.register(lambda: save_profile(force=True))
