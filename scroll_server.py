"""
scroll_server.py - Servidor WebSocket do TP IA
Versão com status real de TP conectado.

Cria/atualiza o arquivo tp_status.json com:
{
  "tp_count": 0,
  "updated": 1234567890.0
}

O painel usa isso para saber se deve:
- mandar reload para TP aberto
- ou abrir o navegador caso nenhum TP esteja conectado
"""

import asyncio
import json
import os
import time
from pathlib import Path

import websockets

TPS = set()
SENDERS = set()

STATUS_FILE = Path("tp_status.json")
BIND_HOST = "0.0.0.0" if os.environ.get("TELEPROMPTER_ALLOW_LAN", "0") == "1" else "127.0.0.1"


def write_status():
    try:
        STATUS_FILE.write_text(
            json.dumps(
                {
                    "tp_count": len(TPS),
                    "remote_count": len(SENDERS),
                    "updated": time.time(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[STATUS] erro ao gravar tp_status.json: {e}", flush=True)


async def send_json(ws, msg):
    try:
        await ws.send(json.dumps(msg))
        return True
    except Exception:
        return False


async def broadcast(targets, msg, exclude=None):
    dead = set()

    for ws in list(targets):
        if ws is exclude:
            continue

        ok = await send_json(ws, msg)

        if not ok:
            dead.add(ws)

    targets.difference_update(dead)
    write_status()


async def handler(websocket):
    path = websocket.request.path if hasattr(websocket, "request") else getattr(websocket, "path", "/")

    if path in ("/sender", "/remote"):
        SENDERS.add(websocket)
        print(f"[SENDER/REMOTE] conectado em {path}", flush=True)

        try:
            async for raw in websocket:
                print("[RECEBIDO]", raw, flush=True)

                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                if msg.get("type") == "control" and msg.get("action") == "reset":
                    msg.setdefault("session_id", f"reset-{time.time_ns()}")

                trace = msg.setdefault("_trace", {})
                trace["t_server_receive"] = time.perf_counter()
                if msg.get("type") == "scroll":
                    print(
                        f"[PIPE 5 #{msg.get('pipe_id')}] SERVER_RECEIVED "
                        f"session={msg.get('session_id')} progress={msg.get('progress')} "
                        f"word_index={msg.get('word_index')}/{msg.get('word_count')}",
                        flush=True,
                    )
                base = trace.get("t_click", trace["t_server_receive"])
                sent = trace.get("t_ws_send", base)
                print(
                    f"[TRACE SERVER] click->server="
                    f"{(trace['t_server_receive'] - base) * 1000:.2f} ms | "
                    f"ws_send->server={(trace['t_server_receive'] - sent) * 1000:.2f} ms",
                    flush=True,
                )

                await broadcast(TPS, msg)

                if msg.get("type") == "control":
                    await broadcast(SENDERS, msg, exclude=websocket)

        except Exception as e:
            print("[SENDER/REMOTE] saiu:", e, flush=True)

        finally:
            SENDERS.discard(websocket)
            print(f"[SENDER/REMOTE] desconectado em {path}", flush=True)
            write_status()

    else:
        TPS.add(websocket)
        write_status()
        print(f"[TP] conectado | total: {len(TPS)}", flush=True)

        try:
            async for raw in websocket:
                print("[TP ENVIOU]", raw, flush=True)

                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                if msg.get("type") == "control":
                    await broadcast(SENDERS, msg)

        except Exception as e:
            print("[TP] saiu:", e, flush=True)

        finally:
            TPS.discard(websocket)
            write_status()
            print(f"[TP] saiu | total: {len(TPS)}", flush=True)


async def main():
    write_status()
    print(f"WebSocket em ws://{BIND_HOST}:8765/", flush=True)
    print("TP      -> ws://IP:8765/", flush=True)
    print("Sender  -> ws://IP:8765/sender", flush=True)
    print("Remote  -> ws://IP:8765/remote", flush=True)

    async with websockets.serve(
        handler,
        BIND_HOST,
        8765,
        ping_interval=None,
        ping_timeout=None,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            STATUS_FILE.write_text(json.dumps({"tp_count": 0, "remote_count": 0, "updated": time.time()}), encoding="utf-8")
        except Exception:
            pass
