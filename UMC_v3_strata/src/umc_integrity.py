"""UMC v3 - integridade de dados: canário, verificação e self-heal (Fase 3).

O que garante (e o que não garante):
 - O transporte de blocos GPU->RAM->disco vive dentro dos .dll nativos, sem
   fonte: não podemos pôr checksums *dentro* da transferência (vê README,
   protocolo da Fase 5). Medimos portanto o resultado de fora: um bloco
   "canário" cujo conteúdo conhecemos é verificado byte-a-byte a cada prompt.
 - Deteccão: divergência de digest -> corrupção ou leitura stale no caminho
   GPU->RAM->GPU (ou no restore a partir do disco).
 - Self-heal: descarrega TODOS os modelos (o disco é a réplica de verdade:
   pesos safetensors intactos) e recria o canário. Máximo MAX_HEALS por
   sessão; depois desliga-se e fica o aviso no log — não se esconde o problema.
 - Round-trip (roundtrip_test): aceitação mensurável — alocar, preencher,
   ler, libertar e reafirmar o digest, N ciclos; exit != 0 em falha.

Camadas de integridade (README): ECC RAM/VRAM não existe nesta GPU (RTX 3060,
GA106 sem ECC) -> deteccão por hash + redundância (disco) + self-heal.

Env:
  UMC_V3_CANARY=0     desliga o canário (default ligado)
  UMC_V3_CANARY_MB    tamanho do canário em MiB (default 16)
"""
import hashlib
import os
import sys
import threading
import time

from umc_common import log

ENABLED = os.environ.get("UMC_V3_CANARY", "1") != "0"
CANARY_MB = max(1, int(os.environ.get("UMC_V3_CANARY_MB", "16") or 16))
PRESSURE_MB = 1024       # só aloca o canário quando a VRAM física fica < 1 GiB
MAX_HEALS = 3            # heals de sessão; ao 4º problema desligamos e avisamos
_OK_LOG_EVERY = 50       # log de sucesso periódico (evita 1 linha por prompt)
_RETRY_S = 60.0          # espera entre tentativas de criação sob pressão

_lock = threading.Lock()
_canary = None           # torch.Tensor uint8 em CUDA (torch só dentro de funções)
_digest = None           # blake2b (hex) do conteúdo esperado
_heals = 0
_verified = 0
_disabled = None         # motivo de desligar o canário (None = activo)
_created = 0.0
_last_try = 0.0
_inactive_logged = False


def _blake2b(data):
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def _physical_free():
    try:
        import umc_bridge_v3
        return umc_bridge_v3.physical_free()
    except Exception:  # noqa: BLE001 - integridade nunca parte o workflow
        return None


def _make_canary(reason):
    """Cria/recria o canário. Devolve True se criado."""
    global _canary, _digest, _created
    import torch
    n = CANARY_MB * 1024 ** 2
    # conteúdo aleatório por geração: se a GPU devolver conteúdo stale de uma
    # geração anterior em vez do padrão novo, o digest diverge e apangamos isso.
    pattern = bytearray(os.urandom(n))
    t = torch.frombuffer(pattern, dtype=torch.uint8).to("cuda")
    got = t.cpu().numpy().tobytes()
    d = _blake2b(bytes(pattern))
    if _blake2b(got) != d:
        log("integrity", "canario nao sobreviveu a alocacao (!) - {}".format(reason))
        return False
    _canary, _digest, _created = t, d, time.time()
    log("integrity", "canario criado ({} MB) - {}".format(CANARY_MB, reason))
    return True


def _heal(reason):
    """Corrupcao confirmada: força releitura dos pesos a partir do disco."""
    global _heals, _canary, _digest, _disabled
    _heals += 1
    log("integrity", "self-heal #{} ({}) - a descarregar todos os modelos"
        .format(_heals, reason))
    if _heals > MAX_HEALS:
        _disabled = "{} heals (max {})".format(_heals, MAX_HEALS)
        log("integrity", "canario DESLIGADO apos {} heals - verificar a memoria"
            " manualmente (vê README, Fase 5)".format(_heals))
        _canary, _digest = None, None
        return False
    _canary, _digest = None, None
    try:
        import comfy.model_management as mm
        mm.unload_all_models()   # pesos voltam do disco na proxima utilizacao
        mm.soft_empty_cache()
    except Exception as e:  # noqa: BLE001
        log("integrity", "unload para heal falhou: {!r}".format(e))
    _make_canary("recriado apos heal #{}".format(_heals))
    return False


def verify_epoch(prompt_id=None):
    """Verifica o canário (e cria-o sob pressão física). True/False/None."""
    global _verified, _last_try, _inactive_logged, _disabled
    if not ENABLED or _disabled:
        return None
    with _lock:
        if _canary is None:
            pf = _physical_free()
            if pf is None:
                if not _inactive_logged:
                    log("integrity", "canario inativo: VRAM fisica nao medida")
                    _inactive_logged = True
                return None
            now = time.time()
            if pf >= PRESSURE_MB * 1024 ** 2:
                return None        # sem pressao: canario adiado (por desenho)
            if now - _last_try < _RETRY_S:
                return None        # pressao persistente e alocacao a falhar
            _last_try = now
            try:
                if not _make_canary("pressao fisica ({} MiB livres)"
                                    .format(pf // 1024 ** 2)):
                    return None
            except Exception as e:  # noqa: BLE001 - ex. OOM sob pressao
                log("integrity", "criacao do canario falhou: {!r}".format(e))
                return None
        try:
            got = _canary.cpu().numpy().tobytes()   # le o caminho GPU->RAM->GPU
        except Exception as e:  # noqa: BLE001
            log("integrity", "canario ilegivel: {!r}".format(e))
            return None
        if _blake2b(got) == _digest:
            _verified += 1
            if _verified == 1 or _verified % _OK_LOG_EVERY == 0:
                log("integrity", "canario OK ({} verificacoes)".format(_verified))
            return True
        log("integrity", "CANARIO CORROMPIDO prompt={} esperado={}... lido={}..."
            .format(prompt_id, _digest[:12], _blake2b(got)[:12]))
        return _heal("digest divergiu")


def roundtrip_test(mb=None, cycles=3):
    """Teste de aceitação da Fase 3: alocar -> preencher -> ler -> libertar.

    Em cada ciclo o bloco é alocado na GPU, preenchido com conteúdo aleatório,
    lido de volta (digest tem de bater certo), submetido a churn do allocator
    (2 blocos de apoio alocados/libertados, que sob pressão física obriga o
    caminho de spill/restore a trabalhar) e lido outra vez. Devolve True só se
    todos os digestos baterem em todos os ciclos; exit != 0 em falha.
    """
    import torch
    mb = mb or CANARY_MB
    n = mb * 1024 ** 2
    ok_all = True
    pf = _physical_free()
    log("roundtrip", "start: {} MB x {} ciclos | VRAM fisica livre: {}".format(
        mb, cycles, "{} MiB".format(pf // 1024 ** 2) if pf is not None else "n/d"))
    for c in range(cycles):
        try:
            pattern = bytes(os.urandom(n))
            d0 = _blake2b(pattern)
            t = torch.frombuffer(bytearray(pattern), dtype=torch.uint8).to("cuda")
            torch.cuda.synchronize()
            d1 = _blake2b(t.cpu().numpy().tobytes())
            fillers = [torch.empty(n, dtype=torch.uint8, device="cuda")
                       for _ in range(2)]
            for f in fillers:
                f.fill_(0xA5)
            torch.cuda.synchronize()
            del fillers
            d2 = _blake2b(t.cpu().numpy().tobytes())
            del t
        except Exception as e:  # noqa: BLE001
            log("roundtrip", "ciclo {}: EXCEPCAO {!r}".format(c + 1, e))
            return False
        ok = d0 == d1 == d2
        ok_all = ok_all and ok
        log("roundtrip", "ciclo {}: {} (d0={} d1={} d2={}...)".format(
            c + 1, "OK" if ok else "FALHOU", d0[:12], d1[:12], d2[:12]))
    log("roundtrip", "resultado: {}".format("OK" if ok_all else "FALHOU"))
    return ok_all


if __name__ == "__main__":
    sys.exit(0 if roundtrip_test() else 1)
