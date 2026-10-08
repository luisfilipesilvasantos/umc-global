#!/usr/bin/env python
"""UMC v3 — baseline medido da Fase 0 (regras: número real, sem mocks, exit != 0 em falha).

Duas partes, correspondentes à workload de baseline aprovada:

  --saturation   Saturação de tensores FORA do ComfyUI (processo standalone,
                 SEM UMC — a injecção só vive no processo ComfyUI): aloca blocos
                 de 256 MiB na GPU até ao OOM/limite, mede por bloco o tempo de
                 aloc+fill, a memória livre vista por cudaMemGetInfo (CUDA) e por
                 pynvml (física real) — quantificando o "mentir ~1 GB" do WDDM —
                 e o ponto em que a física se esgota. Depois liberta tudo e mede a
                 recuperação. Exit 1 se a GPU não respondeu.

  --workflow     Workflow REAL pelo ComfyUI + cadeia UMC v2 de produção
                 (127.0.0.1:8188): núcleo txt2img do final_fight_realistas do
                 utilizador (realisticVisionV51, 30 steps, cfg 7, Euler a,
                 seed 123456 — controlnet omitido porque o ficheiro original
                 está partido: dois JSONs concatenados). Mede duração total,
                 estado do prompt e system_stats antes/depois. Exit 1 se a API
                 estiver em falta ou o prompt falhar.

Saída: linhas [BASELINE] no stdout + JSON em tests/baseline_result.json.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

GB = 1024 ** 3
MB = 1024 ** 2
API = "http://127.0.0.1:8188"
CHUNK_MB = 256
SAT_CAP_GB = 16.0        # tecto de segurança: não estrangular o desktop/WDDM
PHYS_FLOOR_MB = 512      # piso de VRAM física a preservar (desktop + WDDM)
WORKFLOW_TIMEOUT_S = 900
RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "baseline_result.json")

PROMPT_TXT = ("Donald Trump muscular in tank top shaking hands with Ursula von der "
              "Leyen in blue suit, facing Putin and Xi Jinping in tense stance, "
              "realistic lighting, cinematic style, inside subway")
NEG_TXT = "blurry, lowres, duplicate faces, wrong hands, deformed, extra limbs"

# Formato API do ComfyUI — núcleo real do workflow do utilizador (ver docstring).
# Ligações = [node_id, slot_index] INTEIRO (execution.py:921 "slot_index";
# RETURN_TYPES é tuple — nomes de saída dão TypeError na validação).
WORKFLOW = {
    "1": {"class_type": "CheckpointLoaderSimple",
          "inputs": {"ckpt_name": "realisticVisionV51_v51VAE.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": PROMPT_TXT,
                                                     "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG_TXT,
                                                     "clip": ["1", 1]}},
    "4": {"class_type": "EmptyLatentImage",
          "inputs": {"width": 512, "height": 768, "batch_size": 1}},
    "5": {"class_type": "KSampler",
          "inputs": {"model": ["1", 0], "positive": ["2", 0],
                     "negative": ["3", 0], "latent_image": ["4", 0],
                     "seed": 123456, "steps": 30, "cfg": 7.0,
                     "sampler_name": "euler_ancestral", "scheduler": "normal",
                     "denoise": 1.0}},
    "6": {"class_type": "VAEDecode",
          "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    "7": {"class_type": "SaveImage",
          "inputs": {"images": ["6", 0],
                     "filename_prefix": "umc_baseline"}},
}


def line(msg):
    print("[BASELINE] {}".format(msg))  # noqa: T201 - script standalone


def _http(url, payload=None, timeout=30):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class PhysNVML:
    """pynvml uma vez; devolve (free, total) ou (None, None) sem partir nada."""

    def __init__(self):
        self._ok = False
        self._err = None
        try:
            import pynvml  # noqa: F401 - torch traz o pynvml legado
            import pynvml as nv
            nv.nvmlInit()
            self._nv = nv
            self._h = nv.nvmlDeviceGetHandleByIndex(0)
            self._ok = True
        except Exception as e:  # noqa: BLE001
            self._err = repr(e)

    def free_total(self):
        if not self._ok:
            return None, None
        try:
            mi = self._nv.nvmlDeviceGetMemoryInfo(self._h)
            return int(mi.free), int(mi.total)
        except Exception:  # noqa: BLE001
            return None, None


def saturation(result):
    """Aloca até falhar/limite; mede a divergência CUDA vs física real."""
    line("=== saturacao (standalone, SEM UMC — baseline bruta da maquina) ===")
    try:
        import torch
    except Exception as e:  # noqa: BLE001
        line("ERRO: torch indisponivel: {!r}".format(e))
        return False
    if not torch.cuda.is_available():
        line("ERRO: cuda indisponivel")
        return False

    nv = PhysNVML()
    dev = torch.cuda.get_device_name(0)
    props = torch.cuda.get_device_properties(0)
    line("GPU: {} | {} GiB | torch {} | driver CUDA {}".format(
        dev, round(props.total_memory / GB, 2), torch.__version__,
        torch.version.cuda))

    blocks = []
    rows = []
    stop = None
    worst_gap = 0
    worst_row = None
    t0 = time.perf_counter()
    while True:
        allocated = len(blocks) * CHUNK_MB * MB
        if allocated >= SAT_CAP_GB * GB:
            stop = "tecto-seguranca ({} GiB)".format(SAT_CAP_GB)
            break
        cu_free, cu_total = torch.cuda.mem_get_info()
        nv_free, _nv_total = nv.free_total()
        if nv_free is not None and nv_free < PHYS_FLOOR_MB * MB:
            stop = "fisica-abaixo-do-piso ({} MiB)".format(nv_free // MB)
            break
        tb = time.perf_counter()
        try:
            t = torch.empty(CHUNK_MB * MB, dtype=torch.uint8, device="cuda")
            t.fill_(0xA5)
            torch.cuda.synchronize()
        except Exception as e:  # noqa: BLE001 - OOM esperado no fim
            stop = "cuda-erro: {!r}".format(e)
            break
        dt_ms = (time.perf_counter() - tb) * 1000
        blocks.append(t)
        cu_free2, _ = torch.cuda.mem_get_info()
        nv_free2, _ = nv.free_total()
        gap = None
        if cu_free2 is not None and nv_free2 is not None:
            gap = cu_free2 - nv_free2     # quanto a visao CUDA "mente" vs fisica
            if gap > worst_gap:
                worst_gap = gap
                worst_row = len(blocks)
        rows.append({"bloco": len(blocks), "alloc_fill_ms": round(dt_ms, 1),
                     "cuda_livre_MiB": None if cu_free2 is None else cu_free2 // MB,
                     "fisica_livre_MiB": None if nv_free2 is None else nv_free2 // MB,
                     "gap_MiB": None if gap is None else gap // MB})
    alloc_wall = time.perf_counter() - t0

    if not blocks:
        line("ERRO: nenhum bloco alocado — GPU nao respondeu ({})".format(stop))
        return False

    _cf, _ct = torch.cuda.mem_get_info()
    nf, nt = nv.free_total()
    n_blocks = len(blocks)
    aloc_gib = round(n_blocks * CHUNK_MB / 1024, 2)
    line("alocados: {} blocos x {} MiB = {} GiB em {:.1f}s | paragem: {}".format(
        n_blocks, CHUNK_MB, aloc_gib, alloc_wall, stop))
    line("fim: cudaMemGetInfo livre={} MiB | fisica(pynvml) livre={} MiB | "
         "total fisico={} MiB".format(
             _cf // MB, "n/d" if nf is None else nf // MB,
             "n/d" if nt is None else nt // MB))
    if worst_row:
        line("MAIOR DIVERGENCIA CUDA-vs-FISICA: bloco {} -> {} MiB (a visao CUDA "
             "reporta mais livre do que ha fisicamente)".format(
                 worst_row, worst_gap // MB))
    line("ultimas 3 medicoes: {}".format(rows[-3:]))

    t1 = time.perf_counter()
    del blocks
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
    free_ms = (time.perf_counter() - t1) * 1000
    nf2, _ = nv.free_total()
    line("recuperacao: tudo libertado em {:.0f} ms | fisica livre depois={} MiB"
         .format(free_ms, "n/d" if nf2 is None else nf2 // MB))

    result["saturacao"] = {
        "gpu": dev, "vram_total_gib": round(props.total_memory / GB, 2),
        "torch": torch.__version__, "cuda": torch.version.cuda,
        "blocos": n_blocks, "alocado_gib": aloc_gib,
        "alloc_wall_s": round(alloc_wall, 2), "paragem": stop,
        "maior_divergencia_cuda_fisica_MiB": worst_gap // MB,
        "divergencia_no_bloco": worst_row,
        "libertacao_ms": round(free_ms, 0),
        "linhas": rows,
    }
    return True


def _stats():
    return _http(API + "/system_stats", timeout=10)


def workflow(result, timeout_s=WORKFLOW_TIMEOUT_S):
    """Workflow real pela API ComfyUI (cadeia UMC v2 de produção)."""
    line("=== workflow real via API {} (cadeia UMC v2) ===".format(API))
    try:
        before = _stats()
    except Exception as e:  # noqa: BLE001
        line("ERRO: ComfyUI inacessivel em {}: {!r}".format(API, e))
        line("arranca-o com ARRANQUE_UMC_v2_FIX.bat e volta a correr "
             "--workflow")
        return False
    line("ComfyUI activo | system_stats antes: {} ...".format(
        json.dumps(before)[:300]))

    try:
        r = _http(API + "/prompt", {"prompt": WORKFLOW, "client_id": "umc-baseline"},
                  timeout=30)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:800]
        line("ERRO: POST /prompt HTTP {}: {}".format(e.code, body))
        return False
    except Exception as e:  # noqa: BLE001
        line("ERRO: POST /prompt falhou: {!r}".format(e))
        return False
    pid = r.get("prompt_id")
    if not pid:
        line("ERRO: sem prompt_id; resposta: {}".format(json.dumps(r)[:800]))
        return False
    line("prompt submetido: {}".format(pid))

    t0 = time.perf_counter()
    hist = None
    while time.perf_counter() - t0 < timeout_s:
        time.sleep(2)
        try:
            hist = _http(API + "/history/{}".format(pid), timeout=15)
        except Exception as e:  # noqa: BLE001
            line("poll falhou (transitorio): {!r}".format(e))
            continue
        if hist.get(pid):
            entry = hist[pid]
            st = entry.get("status") or {}
            if st.get("completed") or st.get("status_str") in ("success", "error"):
                break
    dur = time.perf_counter() - t0
    if not hist or not hist.get(pid):
        line("ERRO: prompt {} nao terminou em {:.0f}s (timeout)".format(pid, timeout_s))
        return False
    st = (hist[pid].get("status") or {})
    status = st.get("status_str", "?")
    outputs = len((hist[pid].get("outputs") or {}))
    line("terminado em {:.1f}s | status={} | outputs={} node(s)".format(
        dur, status, outputs))
    for m in (st.get("messages") or [])[:8]:
        line("  msg: {}".format(str(m)[:200]))

    try:
        after = _stats()
    except Exception:  # noqa: BLE001
        after = None
    if after:
        line("system_stats depois: {} ...".format(json.dumps(after)[:300]))

    ok = status == "success" and outputs > 0
    result["workflow"] = {
        "prompt_id": pid, "duracao_s": round(dur, 1), "status": status,
        "outputs": outputs, "system_stats_antes": before,
        "system_stats_depois": after,
    }
    if not ok:
        line("ERRO: workflow nao terminou com sucesso")
    return ok


def workflow_reuse(result, pid):
    """Lê do /history um prompt JÁ executado, sem re-submeter.

    Motivo (regra da tarefa: medição sem perturbar): re-submeter o render só
    para cronometrar custa GPU desnecessária — o histórico já tem o que é
    preciso (timestamps das mensagens de execução + outputs).
    """
    line("=== workflow REUTILIZADO do historico: {} ===".format(pid))
    try:
        hist = _http(API + "/history/{}".format(pid), timeout=15)
    except Exception as e:  # noqa: BLE001
        line("ERRO: historico inacessivel em {}: {!r}".format(API, e))
        return False
    entry = hist.get(pid)
    if not entry:
        line("ERRO: prompt {} nao consta no historico".format(pid))
        return False
    st = entry.get("status") or {}
    status = st.get("status_str", "?")
    outputs = len(entry.get("outputs") or {})
    t_start = t_end = None
    for m in (st.get("messages") or []):
        if isinstance(m, list) and len(m) == 2 and isinstance(m[1], dict):
            if m[0] == "execution_start":
                t_start = m[1].get("timestamp")
            elif m[0] == "execution_success":
                t_end = m[1].get("timestamp")
    dur = None
    if t_start and t_end:
        dur = round((t_end - t_start) / 1000.0, 2)
    line("status={} | outputs={} node(s) | duracao_exec={}s".format(
        status, outputs, dur))
    ok = status == "success" and outputs > 0
    result["workflow"] = {
        "prompt_id": pid, "duracao_s": dur, "status": status,
        "outputs": outputs, "reutilizado_do_historico": True,
        # system_stats "antes" do render original não é recuperável do
        # histórico — registar null em vez de inventar medição.
        "system_stats_antes": None, "system_stats_depois": None,
    }
    if not ok:
        line("ERRO: prompt reutilizado nao terminou com sucesso")
    return ok


def main():
    ap = argparse.ArgumentParser(description="baseline UMC (Fase 0)")
    ap.add_argument("--saturation", action="store_true")
    ap.add_argument("--workflow", action="store_true")
    ap.add_argument("--timeout", type=int, default=WORKFLOW_TIMEOUT_S)
    ap.add_argument("--reuse", metavar="PROMPT_ID", default=None,
                    help="workflow: ler do /history em vez de re-submeter")
    a = ap.parse_args()
    if a.reuse:
        a.workflow = True  # --reuse só faz sentido na parte workflow
    if not (a.saturation or a.workflow):
        a.saturation = a.workflow = True

    result = {"quando": time.strftime("%Y-%m-%d %H:%M:%S"),
              "argv": sys.argv[1:], "modo": []}
    ok = True
    if a.saturation:
        result["modo"].append("saturacao")
        ok = saturation(result) and ok
    if a.workflow:
        result["modo"].append("workflow")
        if a.reuse:
            ok = workflow_reuse(result, a.reuse) and ok
        else:
            ok = workflow(result, a.timeout) and ok

    try:
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        line("resultado em {}".format(RESULT_PATH))
    except OSError as e:
        line("AVISO: nao escreveu resultado: {!r}".format(e))
    line("EXIT {}".format("OK" if ok else "FALHOU"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
