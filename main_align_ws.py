# ===== TP LOW CPU ENV PATCH =====
# Precisa ficar antes de importar numpy/faster_whisper/ctranslate2.
# Limita OpenMP/MKL/OpenBLAS no Windows para evitar CTranslate2 em 100% CPU.
import os
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("CT2_NUM_THREADS", "2")
os.environ.setdefault("KMP_BLOCKTIME", "0")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
# =================================

import asyncio
import json
import re
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
import websockets
from faster_whisper import WhisperModel
from rapidfuzz import fuzz

# ===== CONFIG =====
# V5.23 RESET FLAG
DEVICE = 16
INPUT_SR = 16000
CHANNELS = 1
BLOCKSIZE = 1024

WS_URL = "ws://127.0.0.1:8765/sender"
PROJECT_DIR = Path.cwd()
ROTEIRO_PATH = PROJECT_DIR / "roteiro.txt"

# ===== PARAMETROS DE CAPTURA =====
WINDOW_SECONDS = 1.35
STEP_SECONDS = 0.65
GAIN = 12.0
MIN_RMS = 0.0045

# ===== PARAMETROS DE ALINHAMENTO =====
WINDOW_WORDS = 6
MIN_SCORE = 48
MIN_COMMON_WORDS = 2
SMOOTH_FACTOR = 0.16

# Estes são calculados dinamicamente após carregar o roteiro (proporcional ao N)
SEARCH_RADIUS = 12     # sobrescrito após carregar
MAX_STEP_WORDS = 20     # sobrescrito após carregar
MAX_PROGRESS_STEP = 0.09  # sobrescrito após carregar

# Número de matches "parados" antes de forçar avanço
STALL_FORCE_AFTER = 5
STALL_FORCE_WORDS = 3

# ===== ESTABILIZAÇÃO BROADCAST V5.17 =====
MAX_SEND_PROGRESS_PER_SEC = 0.035
MAX_SEND_PROGRESS_BURST = 0.025
PROGRESS_HISTORY_LEN = 3

# Compensação visual: a linha-guia ficava algumas linhas atrás da fala real.
# Este avanço mantém a guia mais próxima do ponto que está sendo lido.
GUIDE_LEAD_WORDS = 10

# Se o match estiver muito à frente e for confiável, permite reposicionamento rápido.
STRONG_MATCH_SCORE = 72
STRONG_MATCH_COMMON = 3

# Evita ficar preso atrás quando a fala já avançou.
MAX_LAG_WORDS = 8

# ===== LOCAL TRACKER V5.21 =====
# O alinhador não busca mais trechos distantes. Ele rastreia próximo do cursor atual.
LOCAL_BACKTRACK_WORDS = 1
LOCAL_LOOKAHEAD_WORDS = 24
LOCAL_MAX_STEP_WORDS = 12
LOCAL_MIN_CONSECUTIVE_HITS = 2
LOCAL_MIN_TOKEN_HITS = 2
LOCAL_MIN_SCORE = 38
LOCAL_STRONG_SCORE = 62


# ===== FILTRO ANTI-FALSO DISPARO V5.19 =====
# Só rola quando a fala tem aderência real ao roteiro.
GOOD_MATCH_STREAK_REQUIRED = 1
MIN_SPOKEN_WORDS_TO_MOVE = 2
STRICT_MIN_SCORE = 42
STRICT_MIN_COMMON_WORDS = 2
OUT_OF_SCRIPT_SCORE = 38
MAX_BAD_STALL_BEFORE_FREEZE_LOG = 3


def norm(s: str) -> str:
    # Remove linhas visuais/de comando antes do alinhamento.
    clean_lines = []
    for line in str(s).splitlines():
        u = line.strip().upper()
        if not line.strip():
            continue
        if u.startswith("[[PAUTA]]"):
            continue
        if u.startswith("//") or u.endswith("//"):
            continue
        clean_lines.append(line)
    s = " ".join(clean_lines).lower()
    s = re.sub(r"[^\w\sáàâãéêíóôõúç]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


# ===== CARREGAR ROTEIRO =====
if not ROTEIRO_PATH.exists():
    raise FileNotFoundError("roteiro.txt não encontrado.")

script_text = ROTEIRO_PATH.read_text(encoding="utf-8")
script_words = norm(script_text).split()
N = len(script_words)
print(f"Roteiro carregado: {N} palavras")


def recalc_params():
    """Recalcula parâmetros de alinhamento proporcionais ao tamanho do roteiro.

    V5.18: mais responsivo para reduzir atraso visual da linha guia.
    """
    global SEARCH_RADIUS, MAX_STEP_WORDS, MAX_PROGRESS_STEP
    # Raio menor evita casar com trecho antigo demais.
    SEARCH_RADIUS = max(10, min(28, int(N * 0.08)))
    # Passo maior permite alcançar a fala real quando a leitura anda rápido.
    MAX_STEP_WORDS = max(10, min(26, int(N * 0.08)))
    # Permite avanço visual mais rápido quando o match é confiável.
    MAX_PROGRESS_STEP = max(0.045, min(0.14, 8.0 / max(1, N)))
    print(f"  params: SEARCH_RADIUS={SEARCH_RADIUS} MAX_STEP={MAX_STEP_WORDS} MAX_PROG={MAX_PROGRESS_STEP:.3f}")


recalc_params()

print("Carregando Whisper...")
model = WhisperModel("tiny", compute_type="int8", cpu_threads=2, num_workers=1)

# ===== BUFFER DE ÁUDIO (thread-safe) =====
_BUFFER_MAXLEN = int(INPUT_SR * 8)
audio_buffer: deque = deque(maxlen=_BUFFER_MAXLEN)
audio_lock = threading.Lock()

# ===== ESTADO DE ALINHAMENTO =====
cursor = 0
last_progress = 0.0
RESET_FLAG_FILE = ".tp_reset.flag"
last_reset_flag_mtime = 0.0
stall_count = 0
last_cursor_at_stall = 0
same_cursor_count = 0
good_match_streak = 0
bad_match_streak = 0
progress_history = deque(maxlen=PROGRESS_HISTORY_LEN)
sent_progress = 0.0
last_send_ts = None
auto_enabled = True
playback_enabled = True
ai_enabled = True
_roteiro_mtime = ROTEIRO_PATH.stat().st_mtime
finished = False
session_id = "initial"
first_word_after_reset = False
pipeline_seq = 0


def state_snapshot(label: str):
    print(
        f"STATE {label}: finished={finished} cursor={cursor}/{N} "
        f"last_progress={last_progress:.3f} sent_progress={sent_progress:.3f} "
        f"last_send_ts={last_send_ts} auto={auto_enabled} "
        f"playback={playback_enabled} ai_enabled={ai_enabled} session={session_id}",
        flush=True,
    )


def reset_alignment_state(progress: float = 0.0, new_session_id=None):
    """Reinicializa, de forma atômica, todo estado derivado do progresso."""
    global cursor, last_progress, stall_count, last_cursor_at_stall
    global same_cursor_count, good_match_streak, bad_match_streak
    global sent_progress, last_send_ts, last_reset_flag_mtime, finished
    global auto_enabled, playback_enabled, ai_enabled, session_id, first_word_after_reset

    state_snapshot("BEFORE_RESET")

    progress = max(0.0, min(1.0, float(progress)))
    cursor = max(0, min(max(0, N - 1), int(progress * max(0, N - 1))))
    last_progress = progress
    sent_progress = progress
    last_send_ts = None
    stall_count = 0
    last_cursor_at_stall = cursor
    same_cursor_count = 0
    good_match_streak = 0
    bad_match_streak = 0
    progress_history.clear()
    finished = progress >= 1.0
    auto_enabled = True
    playback_enabled = True
    ai_enabled = True
    if new_session_id is not None:
        session_id = str(new_session_id)
    first_word_after_reset = True
    with audio_lock:
        audio_buffer.clear()
    state_snapshot("AFTER_RESET")


def reload_roteiro():
    """Carrega explicitamente o roteiro e reinicia o alinhamento uma única vez."""
    global script_words, N, _roteiro_mtime
    try:
        new_text = ROTEIRO_PATH.read_text(encoding="utf-8")
        new_words = norm(new_text).split()
        if not new_words:
            return False
        script_words = new_words
        N = len(script_words)
        _roteiro_mtime = ROTEIRO_PATH.stat().st_mtime
        reset_alignment_state(0.0)
        recalc_params()
        print(f"Roteiro atualizado: {N} palavras", flush=True)
        return True
    except Exception as e:
        print(f"Erro ao recarregar roteiro: {e}")
    return False



def set_cursor_from_progress(progress: float):
    """Sincroniza o cursor interno da IA a partir do progresso atual do TP."""
    if N <= 1:
        return
    reset_alignment_state(progress)
    print(f"SYNC IA: progress={progress:.3f} cursor={cursor}/{N}", flush=True)



def stabilize_progress_for_send(raw_progress: float) -> float:
    """Suaviza e limita o progresso antes de enviar ao TP."""
    global sent_progress, last_send_ts

    now = asyncio.get_running_loop().time()
    raw_progress = max(0.0, min(1.0, float(raw_progress)))
    raw_progress = max(raw_progress, sent_progress)

    progress_history.append(raw_progress)
    averaged = sum(progress_history) / max(1, len(progress_history))
    desired = max(averaged, sent_progress)

    if last_send_ts is None:
        last_send_ts = now
        sent_progress = max(sent_progress, min(desired, sent_progress + MAX_SEND_PROGRESS_BURST))
        return sent_progress

    dt = max(0.10, min(2.0, now - last_send_ts))
    last_send_ts = now

    max_step = MAX_SEND_PROGRESS_PER_SEC * dt + MAX_SEND_PROGRESS_BURST

    if desired > sent_progress + max_step:
        sent_progress += max_step
    else:
        sent_progress = desired

    sent_progress = max(0.0, min(1.0, sent_progress))
    return sent_progress



async def ws_receiver(ws):
    """Recebe comandos do TP/painel/remoto para pausar/retomar/sincronizar a IA."""
    global auto_enabled, playback_enabled, ai_enabled

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            if msg.get("type") != "control":
                continue

            trace = msg.setdefault("_trace", {})
            trace["t_ai_receive"] = time.perf_counter()
            base = trace.get("t_click", trace["t_ai_receive"])
            server_at = trace.get("t_server_receive", base)

            action = msg.get("action")
            mode = msg.get("mode")
            trace["t_ai_process"] = time.perf_counter()

            if mode == "manual":
                auto_enabled = False
                if "progress" in msg:
                    set_cursor_from_progress(float(msg.get("progress", 0)))
                print("IA pausada pelo modo MANUAL", flush=True)

            if mode == "auto":
                auto_enabled = True
                if "progress" in msg:
                    set_cursor_from_progress(float(msg.get("progress", 0)))
                print("IA habilitada pelo modo AUTO", flush=True)

            if action == "pause":
                playback_enabled = False

            if action == "play":
                playback_enabled = True

            if action in ("toggle_play", "play_pause", "playpause"):
                playback_enabled = not playback_enabled

            if action == "sync_cursor":
                if "progress" in msg:
                    set_cursor_from_progress(float(msg.get("progress", 0)))

            if action == "pause_ai":
                playback_enabled = False
                print("IA pausada", flush=True)

            if action == "resume_ai":
                playback_enabled = True
                if "progress" in msg:
                    set_cursor_from_progress(float(msg.get("progress", 0)))
                print("IA retomada", flush=True)

            if action == "reset":
                reset_alignment_state(0.0, msg.get("session_id"))
                print("RESET WS: IA voltou para o início do roteiro.", flush=True)

            if action == "reload":
                reload_roteiro()

            ai_enabled = auto_enabled and playback_enabled
            trace["t_ai_state"] = time.perf_counter()
            if mode in ("auto", "manual"):
                print(
                    f"TRACE IA {mode.upper()}: "
                    f"click->receive={(trace['t_ai_receive'] - base) * 1000:.2f} ms; "
                    f"server->receive={(trace['t_ai_receive'] - server_at) * 1000:.2f} ms; "
                    f"receive->process={(trace['t_ai_process'] - trace['t_ai_receive']) * 1000:.2f} ms; "
                    f"process->state={(trace['t_ai_state'] - trace['t_ai_process']) * 1000:.2f} ms; "
                    f"ai_enabled={ai_enabled}",
                    flush=True,
                )

    except Exception as e:
        print(f"Receiver WS encerrado: {e}", flush=True)



def audio_callback(indata, frames, time, status):
    if status:
        print("sd status:", status)
    chunk = indata[:, 0].astype(np.float32)
    with audio_lock:
        audio_buffer.extend(chunk)


def count_consecutive_hits(spoken_words, window_words):
    """Conta maior sequência de palavras faladas presentes em ordem na janela.

    Isso evita match falso por palavras soltas espalhadas.
    """
    if not spoken_words or not window_words:
        return 0

    best = 0
    current = 0
    wset = set(window_words)

    for word in spoken_words:
        if word in wset:
            current += 1
            best = max(best, current)
        else:
            current = 0

    return best


def local_match_score(spoken, spoken_words, window_words):
    """Score local ponderado.

    Combina:
    - fuzzy score;
    - palavras em comum;
    - sequência consecutiva.
    """
    window = " ".join(window_words)
    spoken_set = set(spoken_words)
    window_set = set(window_words)

    common = len(spoken_set & window_set)
    consecutive = count_consecutive_hits(spoken_words, window_words)
    fuzzy_score = fuzz.token_set_ratio(spoken, window)

    # Peso maior para sequência/conteúdo real.
    combined = fuzzy_score + (common * 8) + (consecutive * 14)
    return combined, fuzzy_score, common, consecutive, window


def check_reset_flag():
    """Verifica se a GUI pediu reset da leitura para o início."""
    global last_reset_flag_mtime

    try:
        flag = PROJECT_DIR / RESET_FLAG_FILE
        if not flag.exists():
            return

        mtime = flag.stat().st_mtime
        if mtime <= last_reset_flag_mtime:
            return

        last_reset_flag_mtime = mtime
        reset_alignment_state(0.0)
        last_reset_flag_mtime = mtime
        print("RESET FLAG: IA voltou para o início do roteiro.")

    except Exception as e:
        print(f"RESET FLAG erro: {e}")


def align_spoken(spoken_text: str, pipe_id=None):
    global cursor, last_progress, stall_count, last_cursor_at_stall, same_cursor_count, good_match_streak, bad_match_streak, finished

    cursor_before = cursor
    if finished:
        print(f"[PIPE 2 #{pipe_id}] ALIGN accepted=False reason=finished cursor={cursor}", flush=True)
        return None

    spoken = norm(spoken_text)
    if not spoken:
        print(f"[PIPE 2 #{pipe_id}] ALIGN accepted=False reason=empty", flush=True)
        return None

    spoken_words = spoken.split()

    if len(spoken_words) < 3:
        print(
            f"[PIPE 2 #{pipe_id}] ALIGN accepted=False reason=fala_curta "
            f"normalized={spoken!r} cursor={cursor}", flush=True,
        )
        return {
            "spoken": spoken,
            "score": 0,
            "progress": last_progress,
            "window": "",
            "blocked": True,
            "reason": "fala curta"
        }

    if N == 0:
        print(f"[PIPE 2 #{pipe_id}] ALIGN accepted=False reason=roteiro_vazio", flush=True)
        return None

    # Busca LOCAL: não permite saltar para trechos distantes do roteiro.
    start = max(0, cursor - LOCAL_BACKTRACK_WORDS)
    end = min(N - 1, cursor + LOCAL_LOOKAHEAD_WORDS)

    best_i = cursor
    best_combined = -1
    best_score = -1
    best_common = 0
    best_consecutive = 0
    best_window = ""

    for i in range(start, end + 1):
        window_words = script_words[i: i + WINDOW_WORDS]
        combined, fuzzy_score, common, consecutive, window = local_match_score(
            spoken, spoken_words, window_words
        )

        # Penaliza match muito atrás do cursor
        if i < cursor:
            combined -= 20

        # Penaliza salto para o fim da janela local
        if i > cursor + LOCAL_MAX_STEP_WORDS:
            combined -= 12

        if combined > best_combined:
            best_combined = combined
            best_score = fuzzy_score
            best_common = common
            best_consecutive = consecutive
            best_i = i
            best_window = window

    # print LOCAL score suprimido LOW CPU

    # Critério de bloqueio:
    # precisa ter aderência mínima real, não só score fuzzy.
    valid_by_sequence = best_consecutive >= LOCAL_MIN_CONSECUTIVE_HITS
    valid_by_common = best_common >= LOCAL_MIN_TOKEN_HITS and best_score >= LOCAL_MIN_SCORE
    strong_fuzzy = best_score >= LOCAL_STRONG_SCORE and best_common >= 2

    if not (valid_by_sequence or valid_by_common or strong_fuzzy):
        bad_match_streak += 1
        good_match_streak = 0
        stall_count += 1
        # print BLOQUEADO suprimido LOW CPU
        print(
            f"[PIPE 2 #{pipe_id}] ALIGN accepted=False reason=fora_janela "
            f"normalized={spoken!r} cursor_before={cursor_before} cursor_after={cursor} "
            f"best_i={best_i} score={best_score:.1f} combined={best_combined:.1f} "
            f"common={best_common} consecutive={best_consecutive} "
            f"threshold_score={LOCAL_MIN_SCORE} search={start}..{end} window={best_window!r}",
            flush=True,
        )
        return {
            "spoken": spoken,
            "score": best_score,
            "progress": last_progress,
            "window": best_window,
            "blocked": True,
            "reason": "fora da janela local"
        }

    bad_match_streak = 0
    good_match_streak += 1
    stall_count = 0

    prev_cursor = cursor

    # Novo cursor sempre próximo: impede teleport.
    lead = 3
    if best_consecutive >= 3:
        lead = 5
    if best_score >= LOCAL_STRONG_SCORE and best_common >= 3:
        lead = 6

    target_cursor = best_i + lead

    # Limite duro por ciclo.
    max_allowed = cursor + LOCAL_MAX_STEP_WORDS
    new_cursor = min(target_cursor, max_allowed)

    # Nunca volta em leitura automática.
    new_cursor = max(new_cursor, cursor)

    # Se encontrou exatamente no cursor, anda devagar para acompanhar leitura.
    if new_cursor == cursor and best_i >= cursor:
        new_cursor = min(cursor + 1, N - 1)

    cursor = min(new_cursor, N - 1)

    if cursor <= prev_cursor:
        same_cursor_count += 1
    else:
        same_cursor_count = 0

    # Avanço leve apenas quando há match local consistente.
    if same_cursor_count >= 3 and good_match_streak >= 2:
        cursor = min(cursor + 1, N - 1)
        same_cursor_count = 0

    progress = cursor / max(1, (N - 1))
    progress = max(progress, last_progress)

    # Suavização no próprio alinhador: no máximo pequeno avanço por análise.
    max_progress_step = max(0.010, min(0.045, (LOCAL_MAX_STEP_WORDS * 1.6) / max(1, N)))
    if progress > last_progress + max_progress_step:
        progress = last_progress + max_progress_step

    last_progress = progress
    if cursor >= N - 1:
        progress = 1.0
        last_progress = 1.0

    print(
        f"[PIPE 2 #{pipe_id}] ALIGN accepted=True normalized={spoken!r} "
        f"cursor_before={cursor_before} cursor_after={cursor} best_i={best_i} "
        f"score={best_score:.1f} combined={best_combined:.1f} common={best_common} "
        f"consecutive={best_consecutive} threshold_score={LOCAL_MIN_SCORE} "
        f"search={start}..{end} progress={progress:.4f} "
        f"word_index={cursor}/{N} window={best_window!r}", flush=True,
    )

    return {
        "spoken": spoken,
        "score": best_score,
        "progress": progress,
        "word_index": cursor,
        "word_count": N,
        "window": best_window,
        "blocked": False
    }


def transcribe_chunk(audio: np.ndarray) -> str:
    audio_g = np.clip(audio * GAIN, -1.0, 1.0)
    segments, _ = model.transcribe(
        audio_g,
        language="pt",
        beam_size=1,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=300),
        condition_on_previous_text=False,
        temperature=0.0,
    )
    return " ".join(s.text.strip() for s in segments).strip().lower()


# ===== LOOP PRINCIPAL =====
async def run_loop(ws):
    global finished, sent_progress, last_send_ts, first_word_after_reset, pipeline_seq
    loop = asyncio.get_running_loop()   # ← forma correta no Python 3.10+
    need = int(INPUT_SR * WINDOW_SECONDS)
    step = int(INPUT_SR * STEP_SECONDS)

    while True:
        # sleep interrompível sem propagar CancelledError acidentalmente
        try:
            await asyncio.sleep(0.20)
        except asyncio.CancelledError:
            raise  # deixa subir normalmente

        check_reset_flag()

        # Se o operador estiver em MANUAL ou o roteiro terminou, a IA fica parada.
        if not ai_enabled:
            continue
        if finished:
            continue

        with audio_lock:
            buf_len = len(audio_buffer)

        if buf_len < need:
            continue

        with audio_lock:
            audio = np.array(list(audio_buffer)[:need], dtype=np.float32)
            for _ in range(min(step, len(audio_buffer))):
                audio_buffer.popleft()

        vol = rms(audio)
        if vol < MIN_RMS:
            continue

        # print volume suprimido LOW CPU

        # Uma transcrição iniciada antes do RESET não pertence à nova sessão.
        transcription_session = session_id
        # Transcrição em thread separada — protege contra CancelledError interno
        try:
            text = await asyncio.shield(
                loop.run_in_executor(None, transcribe_chunk, audio)
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"Erro na transcrição: {e}")
            continue

        if transcription_session != session_id:
            print("Transcrição anterior ao RESET descartada.", flush=True)
            continue

        # print fala suprimido LOW CPU

        if not text:
            continue

        pipeline_seq += 1
        pipe_id = pipeline_seq
        print(f"FALA: {text}", flush=True)
        print(f"[PIPE 1 #{pipe_id}] WHISPER text={text!r} session={session_id}", flush=True)
        result = align_spoken(text, pipe_id=pipe_id)
        if not result:
            continue

        if result.get("blocked"):
            # print BLOCKED suprimido LOW CPU
            continue

        if first_word_after_reset:
            state_snapshot("FIRST_WORD_AFTER_RESET")
            first_word_after_reset = False

        raw_progress = result["progress"]
        if raw_progress >= 1.0:
            progress = 1.0
            sent_progress = 1.0
            last_send_ts = None
            finished = True
        else:
            progress = stabilize_progress_for_send(raw_progress)
        # print progress suprimido LOW CPU

        msg = {
            "type": "scroll",
            "pipe_id": pipe_id,
            "progress": float(progress),
            "word_index": int(result.get("word_index", cursor)),
            "word_count": int(result.get("word_count", N)),
            "session_id": session_id,
        }
        print(
            f"[PIPE 3 #{pipe_id}] SCROLL_CREATED session={session_id} "
            f"progress={progress:.4f} word_index={msg['word_index']}/{msg['word_count']}",
            flush=True,
        )
        try:
            await ws.send(json.dumps(msg))
            print(
                f"[PIPE 4 #{pipe_id}] WS_SENT session={session_id} "
                f"word_index={msg['word_index']}", flush=True,
            )
            print(f"SCROLL IA: {progress:.4f}", flush=True)
            if progress >= 1.0:
                print("FIM DO ROTEIRO: 100%", flush=True)
        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.WebSocketException) as e:
            print(f"WebSocket perdido: {e}")
            raise  # sobe para o loop de reconexão


# ===== RECONEXÃO AUTOMÁTICA =====
async def main():
    with sd.InputStream(
        device=DEVICE,
        channels=CHANNELS,
        samplerate=INPUT_SR,
        blocksize=BLOCKSIZE,
        callback=audio_callback,
    ):
        print("Microfone aberto.")

        while True:
            try:
                print(f"Conectando a {WS_URL} ...")
                async with websockets.connect(
                    WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    print("Conectado ao Teleprompter")
                    receiver_task = asyncio.create_task(ws_receiver(ws))
                    try:
                        await run_loop(ws)
                    finally:
                        receiver_task.cancel()
                        try:
                            await receiver_task
                        except Exception:
                            pass

            except asyncio.CancelledError:
                # Python 3.11+ cancela tarefas desta forma — encerra limpo
                print("Encerrando (CancelledError).")
                break

            except KeyboardInterrupt:
                print("Encerrando.")
                break

            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                OSError,
            ) as e:
                print(f"Desconectado ({e}). Reconectando em 2s...")
                await asyncio.sleep(2)

            except Exception as e:
                print(f"Erro inesperado: {e}. Reconectando em 3s...")
                await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Encerrado pelo usuário.")
