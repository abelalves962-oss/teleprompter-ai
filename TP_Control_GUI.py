"""
TP_Control_GUI_V6_Integrado.py
Interface gráfica para Teleprompter IA — V6 com TP e Remoto integrados.

NOVIDADES V6:
  - Aba "Teleprompter" — exibe o tp.html DENTRO da própria janela via tkinterweb.
    Elimina o navegador externo; isola o problema de rolagem no browser.
  - Aba "Controle Remoto" — painel de controle completo DENTRO da GUI.
  - Aba "Acesso Celular" — exibe o IP da rede local + QR Code para abrir
    o remote.html direto no celular sem digitar nada.
  - O botão "🌐 Abrir TP" ainda abre no browser externo caso preferir.

Dependências extras (instale uma vez):
    pip install tkinterweb qrcode pillow

Como rodar no Windows:
    py -3.11 TP_Control_GUI_V6_Integrado.py

Coloque na mesma pasta:
  scroll_server.py | main_align_ws.py | tp.html | remote.html | roteiro.txt
"""

from __future__ import annotations

import os
import asyncio
import ipaddress
import re
import json
import sys
import time
import queue
import random
import signal
import socket
import shutil
import ctypes
import webbrowser
import threading
import subprocess
from dataclasses import dataclass
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import psutil

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from tkinter.scrolledtext import ScrolledText

# websockets é usado pelo TP nativo para receber comandos em tempo real.
try:
    import websockets  # type: ignore
    WEBSOCKETS_OK = True
except ImportError:
    WEBSOCKETS_OK = False


# tkinterweb é opcional — sem ele a aba TP embutido fica desabilitada.
try:
    import tkinterweb  # type: ignore
    TKWEB_OK = True
except ImportError:
    TKWEB_OK = False


# Pillow também pode ser usado pelo TP nativo para espelhamento real.
try:
    from PIL import Image as PILImage, ImageDraw as PILImageDraw, ImageFont as PILImageFont, ImageTk as PILImageTk  # type: ignore
    PIL_NATIVE_MIRROR_OK = True
except ImportError:
    PIL_NATIVE_MIRROR_OK = False


# qrcode + Pillow são opcionais — sem eles a aba QR fica desabilitada.
try:
    import qrcode                          # type: ignore
    from PIL import Image, ImageTk         # type: ignore
    QR_OK = True
except ImportError:
    QR_OK = False


def tokenize_alignment_text(text: str) -> list[str]:
    """Mesma normalização léxica usada por main_align_ws.norm()."""
    clean_lines = []
    for line in str(text).splitlines():
        upper = line.strip().upper()
        if not line.strip() or upper.startswith("[[PAUTA]]"):
            continue
        if upper.startswith("//") or upper.endswith("//"):
            continue
        clean_lines.append(line)
    normalized = " ".join(clean_lines).lower()
    normalized = re.sub(r"[^\w\sáàâãéêíóôõúç]", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.split() if normalized else []


def build_visual_word_map(text: str, usable_width: int, measure) -> tuple[list[str], list]:
    """Quebra o texto e associa cada linha ao intervalo léxico do alinhador."""
    wrapped = []
    ranges = []
    alignment_index = 0
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        line = ""
        line_start = alignment_index
        line_end = alignment_index - 1
        for word in paragraph.split():
            candidate = (line + " " + word).strip()
            token_count = len(tokenize_alignment_text(word))
            if not line or measure(candidate) <= usable_width:
                line = candidate
                if token_count:
                    line_end = alignment_index + token_count - 1
            else:
                wrapped.append(line)
                ranges.append((line_start, line_end))
                line = word
                line_start = alignment_index
                line_end = alignment_index + token_count - 1
            alignment_index += token_count
        if line:
            wrapped.append(line)
            ranges.append((line_start, line_end))
        wrapped.append("")
        ranges.append(None)
    while wrapped and wrapped[-1] == "":
        wrapped.pop()
        ranges.pop()
    return (wrapped or [""]), (ranges or [None])


APP_TITLE = "Teleprompter AI — Portfolio Stable"
CONFIG_FILE = ".teleprompter_config.json"
WS_PORT   = 8765
HTTP_PORT = 8000
ALLOW_LAN = os.environ.get("TELEPROMPTER_ALLOW_LAN", "0") == "1"
HTTP_HOST = "0.0.0.0" if ALLOW_LAN else "127.0.0.1"
HTTP_OPEN_HOST = "127.0.0.1"


def select_lan_ipv4(interfaces) -> str | None:
    """Seleciona IPv4 utilizável, priorizando Wi-Fi e Ethernet físicos ativos."""
    virtual_markers = (
        "vethernet", "hyper-v", "virtual", "vmware", "virtualbox",
        "loopback", "docker", "wsl", "vpn", "tunnel",
    )
    candidates = []
    for interface_name, is_up, addresses in interfaces:
        name = str(interface_name).lower()
        if not is_up or any(marker in name for marker in virtual_markers):
            continue
        if any(marker in name for marker in ("wi-fi", "wifi", "wireless", "wlan")):
            priority = 0
        elif "ethernet" in name:
            priority = 1
        else:
            priority = 2
        for address in addresses:
            try:
                ip = ipaddress.ip_address(str(address))
            except ValueError:
                continue
            if (
                ip.version != 4 or ip.is_loopback or ip.is_link_local
                or ip.is_unspecified or ip.is_multicast
            ):
                continue
            candidates.append((priority, name, str(ip)))
    return min(candidates, default=(None, None, None))[2]


def build_remote_url(ip: str, port: int, pin: str) -> str:
    """Monta uma URL externa válida; nunca publica loopback ou wildcard."""
    address = ipaddress.ip_address(str(ip))
    if address.version != 4 or address.is_loopback or address.is_unspecified:
        raise ValueError("IPv4 LAN inválido para QR")
    return f"http://{address}:{int(port)}/remote_by.html?pin={pin}"


def mask_remote_url(url: str) -> str:
    return re.sub(r"([?&]pin=)[^&]*", r"\1****", str(url), flags=re.IGNORECASE)

@dataclass
class ManagedProcess:
    name: str
    script: str
    popen: subprocess.Popen | None = None


class QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args):
        pass


# ── Tema visual ────────────────────────────────────────────────────────

def apply_soft_broadcast_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    BG       = "#eef3f8"
    CARD     = "#ffffff"
    BORDER   = "#d7dee8"
    TEXT     = "#1f2937"

    BLUE     = "#9cc9ff"
    GREEN    = "#a7e8c1"
    ORANGE   = "#ffd6a5"
    RED      = "#ffb7b4"
    PURPLE   = "#d6c3ff"
    GRAY_BTN = "#e8e8f3"

    root.configure(bg=BG)
    style.configure(".", font=("Segoe UI", 10))
    style.configure("TFrame",      background=BG)
    style.configure("TLabel",      background=BG, foreground=TEXT)
    style.configure("TLabelframe", background=BG, foreground=TEXT,
                    bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=TEXT,
                    font=("Segoe UI Semibold", 10))
    style.configure("TEntry",    fieldbackground=CARD, background=CARD,
                    foreground=TEXT, bordercolor=BORDER, padding=6)
    style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                    foreground=TEXT, bordercolor=BORDER, padding=5)
    style.configure("TButton",   background=GRAY_BTN, foreground=TEXT,
                    bordercolor=BORDER, focusthickness=0, focuscolor=BG,
                    padding=(12, 8), font=("Segoe UI Semibold", 10))
    style.map("TButton",
              background=[("active", "#dfe7f0"), ("pressed", "#cfd9e6")],
              foreground=[("active", TEXT)])

    def btn(name, bg, active):
        style.configure(name, background=bg, foreground="#172033",
                        bordercolor=BORDER, padding=(12, 8),
                        font=("Segoe UI Semibold", 10))
        style.map(name,
                  background=[("active", active), ("pressed", active)],
                  foreground=[("active", "#172033")])

    btn("Blue.TButton",   BLUE,   "#b6d8ff")
    btn("Green.TButton",  GREEN,  "#bdf0d0")
    btn("Orange.TButton", ORANGE, "#bdffcb")
    btn("Red.TButton",    RED,    "#ffc8c8")
    btn("Purple.TButton", PURPLE, "#e1d2ff")

    style.configure("Horizontal.TScale", background=BG, troughcolor="#d7dee8",
                    bordercolor=BG, lightcolor=BG, darkcolor=BG)


def apply_status_styles(root):
    style = ttk.Style(root)
    bg = "#ffffff"
    style.configure("StatusGood.TLabel",    background=bg, foreground="#00875a",
                    font=("Segoe UI Semibold", 10))
    style.configure("StatusWarn.TLabel",    background=bg, foreground="#d68910",
                    font=("Segoe UI Semibold", 10))
    style.configure("StatusBad.TLabel",     background=bg, foreground="#c0392b",
                    font=("Segoe UI Semibold", 10))
    style.configure("StatusNeutral.TLabel", background=bg, foreground="#7a6e96",
                    font=("Segoe UI Semibold", 10))

    # Estilos para o painel remoto integrado
    style.configure("RemotePrimary.TButton",
                    background="#2563eb", foreground="#ffffff",
                    padding=(14, 12), font=("Segoe UI Semibold", 12))
    style.map("RemotePrimary.TButton",
              background=[("active", "#1d4ed8"), ("pressed", "#1e40af")],
              foreground=[("active", "#ffffff")])

    style.configure("RemoteGreen.TButton",
                    background="#16a34a", foreground="#ffffff",
                    padding=(14, 12), font=("Segoe UI Semibold", 12))
    style.map("RemoteGreen.TButton",
              background=[("active", "#15803d"), ("pressed", "#166534")],
              foreground=[("active", "#ffffff")])

    style.configure("RemoteYellow.TButton",
                    background="#d97706", foreground="#ffffff",
                    padding=(14, 12), font=("Segoe UI Semibold", 12))
    style.map("RemoteYellow.TButton",
              background=[("active", "#b45309"), ("pressed", "#92400e")],
              foreground=[("active", "#ffffff")])

    style.configure("RemoteRed.TButton",
                    background="#dc2626", foreground="#ffffff",
                    padding=(14, 12), font=("Segoe UI Semibold", 12))
    style.map("RemoteRed.TButton",
              background=[("active", "#b91c1c"), ("pressed", "#991b1b")],
              foreground=[("active", "#ffffff")])

    style.configure("RemotePurple.TButton",
                    background="#7c3aed", foreground="#ffffff",
                    padding=(14, 12), font=("Segoe UI Semibold", 12))
    style.map("RemotePurple.TButton",
              background=[("active", "#6d28d9"), ("pressed", "#5b21b6")],
              foreground=[("active", "#ffffff")])

    style.configure("RemoteHold.TButton",
                    background="#374151", foreground="#ffffff",
                    padding=(14, 12), font=("Segoe UI Semibold", 12))
    style.map("RemoteHold.TButton",
              background=[("active", "#1f2937"), ("pressed", "#111827")],
              foreground=[("active", "#ffffff")])




def get_windows_monitors():
    """Retorna monitores no Windows como lista de dicts.

    Correção aplicada:
    - o callback do Windows precisa receber uma estrutura RECT;
    - antes estava como POINTER(c_long), por isso dava:
      AttributeError: 'c_long' object has no attribute 'left'
    """
    monitors = []

    try:
        if os.name == "nt":
            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            MONITORENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_int,
                ctypes.c_ulong,
                ctypes.c_ulong,
                ctypes.POINTER(RECT),
                ctypes.c_double
            )

            def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                r = lprcMonitor.contents
                left, top, right, bottom = r.left, r.top, r.right, r.bottom
                monitors.append({
                    "left": int(left),
                    "top": int(top),
                    "right": int(right),
                    "bottom": int(bottom),
                    "width": int(right - left),
                    "height": int(bottom - top),
                })
                return 1

            user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(callback), 0)

    except Exception:
        monitors = []

    monitors.sort(key=lambda m: (m["left"], m["top"]))
    return monitors


def monitor_containing_geometry(x: int, y: int, monitors: list[dict]):
    """Escolhe o monitor que contém o ponto x/y; fallback para o primeiro."""
    if not monitors:
        return None

    for m in monitors:
        if m["left"] <= x < m["right"] and m["top"] <= y < m["bottom"]:
            return m

    return monitors[0]



# ── TP Nativo em Canvas ────────────────────────────────────────────────

class NativeTPWindow(tk.Toplevel):
    """Janela flutuante nativa para o Teleprompter.
    """

    def __init__(self, master, project_dir_func, log_func):
        super().__init__(master)
        self.project_dir_func = project_dir_func
        self.log = log_func

        self.title("Teleprompter IA — Native Broadcast")
        self.configure(bg="black")

        # Controle de monitores / fullscreen.
        self.monitors = get_windows_monitors()
        self.normal_geometry = None
        self.fullscreen = False
        self.current_monitor_index = 0

        self._place_on_monitor(0, fullscreen=False)
        self.minsize(800, 450)

        self.mirror = False
        self.auto_mode = True
        self.playing = True

        self.progress = 0.0
        self.target_progress = 0.0
        self.pending_progress = 0.0
        self.last_received_progress = 0.0
        self.last_accepted_progress = 0.0
        self.last_word_index = None
        self.last_word_count = 0
        self.target_line_index = None
        self.line_word_ranges = []
        self.session_id = "initial"
        self.first_scroll_after_reset = False
        self._pending_pipe_motor_log = None
        self._motor_debug_frames = 0
        self.display_progress = 0.0
        self.finished = False
        self.ignore_ai_scroll_until = 0.0
        self.warmup_until = 0.0
        self.warmup_frames = 0
        self.manual_speed = 0.0004  # começa lento; slider ajusta em tempo real
        self.max_ai_step = 0.028
        self.max_render_step = 0.010
        self.native_image_ref = None
        self.FAST_CATCHUP_THRESHOLD = 0.025
        # Otimização CPU: 30 FPS e redução de redraw desnecessário.
        self.render_interval_ms = 16  # ~60 FPS para rolagem fluida
        self._last_draw_progress = -1.0
        self._last_draw_size = (0, 0)
        self._last_draw_auto_mode = None
        # Motor de rolagem suave para fonte grande.
        self.smooth_gain = 0.085
        self.max_pixels_per_frame = 5.0
        self.catchup_pixels_per_frame = 8.0
        self._scroll_vel_px = 0.0
        self._last_frame_ts = time.time()
        self._last_scroll_trace_ts = 0.0

        self.font_size = 56          # Padrão broadcast: 52–60pt cabe ~6–8 palavras por linha
        self.font_family = "Arial"
        self.line_gap = 32           # Entrelinha maior = mais respiro entre as linhas
        self.side_margin = 100       # Margem de segurança broadcast (safe area lateral)
        self.text_align_left = True
        self.active_line_offset_lines = -0.5   # linha ativa alinhada à guia, sem deslocamento

        self.raw_script_text = ""
        self._last_wrap_width = 0
        self._last_wrap_width = 0
        self.lines = []
        self.line_height = 78
        self.total_height = 1

        self.ws_thread = None
        self.ws_stop = threading.Event()
        self.msg_queue: queue.Queue[dict] = queue.Queue()
        self._last_payload_key = None
        self._last_payload_ts = 0.0
        self._last_event_id = None
        self._recent_event_ids = []

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.protocol("WM_DELETE_WINDOW", self.close)

        # Atalhos
        for widget in (self, self.canvas):
            widget.bind("<F11>", self.toggle_fullscreen)
            widget.bind("<Escape>", self.exit_fullscreen)
            widget.bind("<space>", lambda _e: self.toggle_play())
            widget.bind("<r>", lambda _e: self.reload_script())
            widget.bind("<plus>", lambda _e: self.font_plus())
            widget.bind("<minus>", lambda _e: self.font_minus())
            widget.bind("<Up>", lambda _e: self.manual_step(-0.01))
            widget.bind("<Down>", lambda _e: self.manual_step(0.01))
            widget.bind("<Control-Key-1>", lambda _e: self.move_to_monitor(0))
            widget.bind("<Control-Key-2>", lambda _e: self.move_to_monitor(1))

        self.canvas.focus_set()

        self.reload_script()
        self.start_ws_listener()
        self.after(self.render_interval_ms, self.render_loop)

    def _capture_position_anchor(self):
        """Guarda a linha atual para preservar posição ao trocar monitor/fonte."""
        try:
            if not self.lines:
                return None
            idx = int(round(self.progress * max(1, len(self.lines) - 1)))
            idx = max(0, min(len(self.lines) - 1, idx))
            line = self.lines[idx].strip()
            return line or None
        except Exception:
            return None

    def _restore_position_anchor(self, anchor, fallback_progress: float):
        """Restaura posição procurando a mesma linha após rewrap."""
        try:
            if anchor and self.lines:
                a = re.sub(r"\s+", " ", anchor.strip()).upper()
                for i, line in enumerate(self.lines):
                    l = re.sub(r"\s+", " ", line.strip()).upper()
                    if l and (l == a or a in l or l in a):
                        p = i / max(1, len(self.lines) - 1)
                        self.progress = max(0.0, min(1.0, p))
                        self.target_progress = self.progress
                        self._scroll_vel_px = 0.0
                        return
            self.progress = max(0.0, min(1.0, fallback_progress))
            self.target_progress = self.progress
            self._scroll_vel_px = 0.0
        except Exception:
            self.progress = max(0.0, min(1.0, fallback_progress))
            self.target_progress = self.progress

    def _on_canvas_resize(self, event=None):
        """Recalcula quebra de linha somente quando a largura muda de verdade.

        Evita ficar recalculando linhas durante a leitura, o que podia travar
        o TP perto do final do roteiro.
        """
        try:
            if not getattr(self, "raw_script_text", ""):
                return

            width = max(1, self.canvas.winfo_width())
            if abs(width - getattr(self, "_last_wrap_width", 0)) < 180:
                return

            self._last_wrap_width = width
            current_progress = self.progress
            anchor = self._capture_position_anchor()

            self.lines = self.wrap_text_to_lines(self.raw_script_text)
            self.recalc_metrics()

            self._restore_position_anchor(anchor, current_progress)
            if self.last_word_index is not None:
                self._apply_word_target(self.last_word_index, self.last_word_count)
            self._last_draw_progress = -1.0
            self._last_draw_size = (0, 0)
            self.draw()
        except Exception:
            pass

    # ── Monitores / Fullscreen ──────────────────────────────────────

    def _fallback_monitor(self):
        w = max(800, self.winfo_screenwidth())
        h = max(450, self.winfo_screenheight())
        return {"left": 0, "top": 0, "right": w, "bottom": h, "width": w, "height": h}

    def _place_on_monitor(self, index: int = 0, fullscreen: bool = False):
        """Posiciona a janela inteira dentro do monitor informado."""
        monitors = self.monitors or [self._fallback_monitor()]
        index = max(0, min(index, len(monitors) - 1))
        self.current_monitor_index = index
        m = monitors[index]

        if fullscreen:
            self.overrideredirect(True)
            self.geometry(f'{m["width"]}x{m["height"]}+{m["left"]}+{m["top"]}')
            self.lift()
            self.focus_force()
            self.canvas.focus_set()
        else:
            # Abre a janela ocupando quase todo o monitor, mas com borda para arrastar.
            w = max(800, int(m["width"] * 0.92))
            h = max(450, int(m["height"] * 0.86))
            x = int(m["left"] + (m["width"] - w) / 2)
            y = int(m["top"] + (m["height"] - h) / 2)
            self.overrideredirect(False)
            self.geometry(f"{w}x{h}+{x}+{y}")
            self.lift()
            self.focus_force()

    def _current_monitor(self):
        """Detecta em qual monitor a janela está pelo centro da janela."""
        monitors = self.monitors or [self._fallback_monitor()]
        try:
            x = self.winfo_x() + int(self.winfo_width() / 2)
            y = self.winfo_y() + int(self.winfo_height() / 2)
            m = monitor_containing_geometry(x, y, monitors)
            return m or monitors[0]
        except Exception:
            return monitors[0]

    def move_to_monitor(self, index: int):
        """Move a janela para o monitor solicitado."""
        self.exit_fullscreen()
        self._place_on_monitor(index, fullscreen=False)
        self.log(f"TP nativo movido para monitor {index + 1}.")


    # ── Texto / formatação ───────────────────────────────────────────

    def normalize_text(self, raw: str) -> str:
        raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
        raw = re.sub(r"[ ]{2,}", " ", raw).strip()

        lines = [l.strip() for l in raw.split("\n")]
        cleaned = []
        empty = 0

        for line in lines:
            upper = line.upper()
            if upper.startswith("[[PAUTA]]") or upper.startswith("//") or upper.endswith("//"):
                continue
            if not line:
                empty += 1
                if empty <= 1:
                    cleaned.append("")
            else:
                empty = 0
                cleaned.append(line)

        paragraphs = []
        current = []

        for line in cleaned:
            if not line:
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                continue
            current.append(line)

        if current:
            paragraphs.append(" ".join(current))

        return "\n\n".join(p.strip() for p in paragraphs if p.strip())

    def start_warmup(self, seconds: float = 2.8):
        """Aquece o TP após abrir/recarregar roteiro.

        Durante o warm-up:
        - ignora scrolls antigos da IA;
        - mantém o TP parado no início;
        - permite o Canvas recalcular linhas, altura e renderização;
        - depois libera a leitura automática.
        """
        try:
            self.warmup_until = time.time() + float(seconds)
            self.ignore_ai_scroll_until = self.warmup_until
            self.warmup_frames = 0
            self.playing = False
            self.progress = 0.0
            self.target_progress = 0.0
            self.display_progress = 0.0
            self.pending_progress = 0.0
            self.last_received_progress = 0.0
            self.last_accepted_progress = 0.0
            self.last_word_index = None
            self.last_word_count = 0
            self.target_line_index = None
            self.after(int(seconds * 1000), self.finish_warmup)
        except Exception:
            pass

    def finish_warmup(self):
        """Libera o TP após o warm-up inicial."""
        try:
            self.playing = True
            self.auto_mode = True
            self.finished = False
            self.ignore_ai_scroll_until = 0.0
            self.warmup_until = 0.0
            self.log("TP nativo: warm-up concluído, AUTO liberado.")
        except Exception:
            pass

    def reload_script(self):
        try:
            roteiro = self.project_dir_func() / "roteiro.txt"
            if not roteiro.exists():
                text = "roteiro.txt não encontrado."
            else:
                text = roteiro.read_text(encoding="utf-8", errors="replace")

            text = self.normalize_text(text)
            self.raw_script_text = text
            self.lines = self.wrap_text_to_lines(text)
            self.recalc_metrics()

            # Zera cursor e velocidade de rolagem.
            self.auto_mode = True
            self.playing = True
            self.progress = 0.0
            self.target_progress = 0.0
            self.display_progress = 0.0
            self.pending_progress = 0.0
            self.last_received_progress = 0.0
            self.last_accepted_progress = 0.0
            self.last_word_index = None
            self.last_word_count = 0
            self.target_line_index = None
            self.finished = False
            self._scroll_vel_px = 0.0
            self._last_ai_progress_time = 0.0

            # Invalida todos os caches de draw para forçar redesenho imediato.
            self._last_draw_progress = -1.0
            self._last_draw_size = (0, 0)
            self._mirror_pil_last_progress = -1.0
            self._mirror_pil_last_size = (0, 0)

            self.canvas.delete("all")
            self.draw()
            self.log("TP nativo: roteiro carregado e cursor zerado.")
        except Exception as e:
            self.lines = [f"Erro ao carregar roteiro: {e}"]
            self.log(f"TP nativo erro ao carregar roteiro: {e}")

    @staticmethod
    def alignment_tokens(text: str) -> list[str]:
        """Tokenização equivalente a norm(...).split() do alinhador."""
        return tokenize_alignment_text(text)

    def wrap_text_to_lines(self, text: str) -> list[str]:
        """Quebra linhas usando a largura real da fonte.

        Correção:
        - antes a quebra era feita por quantidade aproximada de caracteres;
        - com texto alinhado à esquerda, algumas linhas ficavam maiores que a tela;
        - agora a largura é medida em pixels com tkfont.Font.measure().
        """
        import tkinter.font as tkfont

        width = max(600, self.winfo_width() or 1280)
        usable = max(300, width - (self.side_margin * 2))

        try:
            measure_font = tkfont.Font(
                family=self.font_family,
                size=self.font_size,
                weight="bold"
            )
        except Exception:
            measure_font = None

        if measure_font is not None:
            measure = measure_font.measure
        else:
            char_width = max(8, self.font_size * 0.58)
            measure = lambda value: len(value) * char_width

        wrapped, ranges = build_visual_word_map(text, usable, measure)
        self.line_word_ranges = ranges
        return wrapped

    def recalc_metrics(self):
        self.line_height = int(self.font_size * 1.22 + self.line_gap)
        self.total_height = max(1, len(self.lines) * self.line_height)

    # ── WebSocket receiver ───────────────────────────────────────────

    def start_ws_listener(self):
        if not WEBSOCKETS_OK:
            self.log("TP nativo: biblioteca websockets ausente. Instale requirements.txt.")
            return

        if self.ws_thread and self.ws_thread.is_alive():
            return

        self.ws_stop.clear()
        self.ws_thread = threading.Thread(target=self._ws_thread_main, daemon=True)
        self.ws_thread.start()
        self.log("TP nativo: WebSocket listener OK.")

    def _ws_thread_main(self):
        try:
            asyncio.run(self._ws_loop())
        except Exception as e:
            self.log(f"TP nativo WebSocket finalizado: {e}")

    async def _ws_loop(self):
        """Conecta ao WebSocket para receber comandos do servidor.

        Compatibilidade:
        - alguns scroll_server usam /tp para tela de TP;
        - outros repassam mensagens para /sender;
        - esta versão tenta os dois caminhos e reconecta automaticamente.
        """
        endpoints = [
            f"ws://127.0.0.1:{WS_PORT}/tp",
            f"ws://127.0.0.1:{WS_PORT}/sender",
            f"ws://localhost:{WS_PORT}/tp",
            f"ws://localhost:{WS_PORT}/sender",
        ]

        while not self.ws_stop.is_set():
            connected = False

            for uri in endpoints:
                if self.ws_stop.is_set():
                    break

                try:
                    async with websockets.connect(uri, ping_interval=10, ping_timeout=10) as ws:
                        connected = True
                        self.log(f"TP nativo conectado ao WebSocket: {uri}")

                        async for msg in ws:
                            if self.ws_stop.is_set():
                                break

                            try:
                                data = json.loads(msg)
                                self.msg_queue.put(data)
                            except Exception:
                                pass

                except Exception:
                    # tenta próximo endpoint
                    await asyncio.sleep(0.25)

            if not connected:
                await asyncio.sleep(1.0)

    def process_messages(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self.handle_message(msg)
        except queue.Empty:
            pass

    def handle_message(self, msg: dict):
        mtype = msg.get("type")

        if mtype == "scroll":
            pipe_id = msg.get("pipe_id")
            msg_session = msg.get("session_id")
            if msg_session is not None and str(msg_session) != self.session_id:
                self.log(
                    f"[PIPE 6 #{pipe_id}] TP_RECEIVED tp_session={self.session_id} "
                    f"packet_session={msg_session} accepted=False reason=session_mismatch"
                )
                return
            self.log(
                f"[PIPE 6 #{pipe_id}] TP_RECEIVED tp_session={self.session_id} "
                f"packet_session={msg_session} accepted=True reason=session_match"
            )
            if "progress" in msg:
                p = float(msg.get("progress", 0.0))
                self.set_progress(
                    p,
                    word_index=msg.get("word_index"),
                    word_count=msg.get("word_count"),
                )
                self._last_ai_progress_time = time.time()  # IA ativa
                mapping = self._line_for_word(self.last_word_index) if self.last_word_index is not None else None
                line_index = mapping[0] if mapping else None
                visual_height = max(1, max(0, len(self.lines) - 1) * self.line_height)
                self.log(
                    f"[PIPE 7 #{pipe_id}] WORD_MAP word_index={self.last_word_index}/"
                    f"{self.last_word_count} line_index={line_index} "
                    f"target_y={self.target_progress * visual_height:.1f}"
                )
                self._pending_pipe_motor_log = pipe_id
                self._motor_debug_frames = 6
                if self.first_scroll_after_reset:
                    self._state_snapshot("FIRST_SCROLL_AFTER_RESET")
                    self.first_scroll_after_reset = False

        elif mtype == "control":
            trace = msg.setdefault("_trace", {})
            trace["t_native_receive"] = time.perf_counter()
            action = msg.get("action")
            mode = msg.get("mode")
            event_id = msg.get("event_id")
            event_source = msg.get("_source", "local")
            recent_event_ids = getattr(self, "_recent_event_ids", [])
            duplicate_event = bool(event_id and event_id in recent_event_ids)
            self.log(
                f"CONTROL_EVENT event_id={event_id or '-'} source={event_source} "
                f"duplicate={duplicate_event}"
            )
            if duplicate_event:
                return
            if event_id:
                self._last_event_id = event_id
                recent_event_ids.append(event_id)
                self._recent_event_ids = recent_event_ids[-128:]

            if mode == "auto":
                old_mode = self.auto_mode
                self.auto_mode = True
                self.log(f"MODE_CHANGE source=control old={'AUTO' if old_mode else 'MANUAL'} new=AUTO")
                # Retoma suavemente somente o último índice confirmado; eventos
                # intermediários recebidos em MANUAL não viram movimentos visuais.
                if self.last_word_index is not None:
                    self._apply_word_target(self.last_word_index, self.last_word_count)
                else:
                    self.target_progress = max(self.progress, self.pending_progress)

            elif mode == "manual":
                old_mode = self.auto_mode
                self.auto_mode = False
                self.log(f"MODE_CHANGE source=control old={'AUTO' if old_mode else 'MANUAL'} new=MANUAL")
                self.target_progress = self.progress
                self.target_line_index = int(round(self.progress * max(1, len(self.lines) - 1)))
                self._scroll_vel_px = 0.0

            if mode in ("auto", "manual"):
                trace["t_native_state"] = time.perf_counter()
                self._pending_mode_trace = (mode, dict(trace))
                base = trace.get("t_click", trace["t_native_receive"])
                self.log(
                    f"TRACE {mode.upper()} TP estado: "
                    f"click→receive={(trace['t_native_receive'] - base) * 1000:.2f} ms; "
                    f"receive→state={(trace['t_native_state'] - trace['t_native_receive']) * 1000:.2f} ms"
                )

            if "speed" in msg:
                try:
                    self.manual_speed = max(0.0002, min(0.0040, float(msg["speed"])))
                except Exception:
                    pass

            # Ações explícitas. Preferir estado determinístico.
            if action == "play":
                self.playing = True

            elif action == "pause":
                self.playing = False

            elif action in ("toggle_play", "play_pause", "playpause"):
                self.toggle_play()

            elif action == "reset":
                self._state_snapshot("BEFORE_RESET")
                if msg.get("session_id") is not None:
                    self.session_id = str(msg["session_id"])
                self.reload_script()
                self.auto_mode = True
                self.playing = True
                self.progress = 0.0
                self.target_progress = 0.0
                self.pending_progress = 0.0
                self.last_received_progress = 0.0
                self.last_accepted_progress = 0.0
                self.last_word_index = None
                self.last_word_count = 0
                self.target_line_index = None
                self.first_scroll_after_reset = True
                self.display_progress = 0.0
                self.finished = False
                self.draw()
                self._state_snapshot("AFTER_RESET")

            elif action == "forward":
                self.manual_step(float(msg.get("value", 0.008)))

            elif action == "back":
                self.manual_step(-float(msg.get("value", 0.008)))

            elif action == "reload":
                self.reload_script()

            elif action == "toggle_mirror":
                self.toggle_mirror()

            elif action == "font_plus":
                self.font_plus()

            elif action == "font_minus":
                self.font_minus()


    # ── Controle ─────────────────────────────────────────────────────

    def _state_snapshot(self, label: str):
        visual_height = max(1, max(0, len(self.lines) - 1) * self.line_height)
        self.log(
            f"STATE {label}: finished={self.finished} progress={self.progress:.3f} "
            f"target={self.target_progress:.3f} pending={self.pending_progress:.3f} "
            f"last_received={self.last_received_progress:.3f} "
            f"word_index={self.last_word_index} line={self.target_line_index} "
            f"current_y={self.progress * visual_height:.1f} "
            f"target_y={self.target_progress * visual_height:.1f} "
            f"velocity={self._scroll_vel_px:.2f} auto={self.auto_mode} "
            f"playing={self.playing} display_progress={self.display_progress:.3f} "
            f"session={self.session_id}"
        )

    def _final_target_y(self) -> float:
        return float(max(1, max(0, len(self.lines) - 1) * self.line_height))

    def _update_display_progress(self):
        final_y = self._final_target_y()
        current_y = self.progress * final_y
        self.display_progress = max(0.0, min(1.0, current_y / final_y))

    def _line_for_word(self, word_index: int):
        """Retorna (índice da linha, início, fim) que contém a palavra."""
        if not self.line_word_ranges:
            return None
        word_index = max(0, int(word_index))
        last_valid = None
        for line_index, word_range in enumerate(self.line_word_ranges):
            if word_range is None:
                continue
            start, end = word_range
            last_valid = (line_index, start, end)
            if start <= word_index <= end:
                return last_valid
        return last_valid

    def _apply_word_target(self, word_index: int, word_count: int = 0, update_target: bool = True):
        """Converte palavra lógica em alvo visual interpolado dentro da linha."""
        mapping = self._line_for_word(word_index)
        if mapping is None:
            return False
        line_index, line_word_start, line_word_end = mapping
        normalized_word_index = max(0, int(word_index))
        is_new_word = normalized_word_index != self.last_word_index
        self.last_word_index = normalized_word_index
        self.last_word_count = max(0, int(word_count or 0))
        self.target_line_index = line_index
        if update_target:
            words_in_line = max(1, line_word_end - line_word_start + 1)
            word_offset = max(0, min(words_in_line - 1, normalized_word_index - line_word_start))
            fraction = word_offset / words_in_line
            final_y = self._final_target_y()
            base_y = line_index * self.line_height
            interpolated_y = min(final_y, base_y + (fraction * self.line_height))
            interpolated_progress = interpolated_y / final_y
            self.target_progress = max(
                self.target_progress,
                self.progress,
                min(1.0, interpolated_progress),
            )
            if is_new_word:
                self.log(
                    f"WORD_INTERPOLATION word_index={normalized_word_index} line={line_index} "
                    f"line_word_start={line_word_start} line_word_end={line_word_end} "
                    f"fraction={fraction:.3f} base_y={base_y:.1f} "
                    f"target_y={self.target_progress * final_y:.1f}"
                )
        return True

    def set_progress(self, p: float, word_index=None, word_count=None):
        """Preserva integralmente o último alvo válido recebido da IA.

        ``progress`` é a posição renderizada; ``target_progress`` é o destino
        do motor; ``pending_progress`` é o último destino confirmado pela IA.
        A suavização acontece exclusivamente no motor de scroll.
        """
        if time.time() < getattr(self, "ignore_ai_scroll_until", 0.0):
            return

        p = max(0.0, min(1.0, float(p)))
        if self.finished and p >= 1.0:
            return

        # Ignora regressões grandes do alinhamento, mas aceita avanço.
        if p < self.last_received_progress - 0.020:
            return

        self.last_accepted_progress = max(self.last_accepted_progress, p)
        self.last_received_progress = p
        self.pending_progress = max(self.pending_progress, p)
        used_word_target = False
        if word_index is not None:
            try:
                used_word_target = self._apply_word_target(
                    int(word_index), int(word_count or 0), update_target=self.auto_mode
                )
            except (TypeError, ValueError):
                used_word_target = False
        if not used_word_target:
            self.target_line_index = None
            self.target_progress = self.pending_progress
        if p >= 1.0:
            self.pending_progress = 1.0
            self.target_progress = 1.0

        self._log_scroll_tracking(force=True)

    def _log_scroll_tracking(self, force: bool = False):
        """Registra a perseguição do alvo sem interferir no motor de scroll."""
        now = time.perf_counter()
        if not force and now - getattr(self, "_last_scroll_trace_ts", 0.0) < 0.25:
            return
        if not force and abs(self.target_progress - self.progress) < 0.0001:
            return

        self._last_scroll_trace_ts = now
        visual_height = max(1, max(0, len(self.lines) - 1) * self.line_height)
        scroll_y = self.progress * visual_height
        target_y = self.target_progress * visual_height
        mapping = self._line_for_word(self.last_word_index) if self.last_word_index is not None else None
        line_index, line_start, line_end = mapping if mapping else (-1, -1, -1)
        line_y = line_index * self.line_height if line_index >= 0 else -1
        self.log(
            "SCROLL_TRACK "
            f"WORD_INDEX={self.last_word_index}/{self.last_word_count} "
            f"LINE={line_index} WORDS={line_start}..{line_end} LINE_Y={line_y:.1f} "
            f"TARGET_Y={target_y:.1f} CURRENT_Y={scroll_y:.1f} "
            f"PROGRESS={self.last_received_progress:.3f}"
        )

    def _advance_scroll_state(self, dt: float):
        """Executa um frame do motor existente em direção ao alvo preservado."""
        visual_height = max(1, max(0, len(self.lines) - 1) * self.line_height)
        debug_motor = getattr(self, "_motor_debug_frames", 0) > 0
        if debug_motor:
            entry_tag = "[MOTOR_MANUAL_ENTRY]" if not self.auto_mode else "[MOTOR_ENTRY]"
            self.log(
                f"{entry_tag} "
                f"auto={self.auto_mode} playing={self.playing} finished={self.finished} "
                f"current_y={self.progress * visual_height:.2f} "
                f"target_y={self.target_progress * visual_height:.2f} "
                f"velocity={self._scroll_vel_px:.3f} progress={self.progress:.6f} "
                f"target_progress={self.target_progress:.6f}"
            )
        if not self.playing:
            if debug_motor:
                skip_tag = "[MOTOR_MANUAL_SKIP]" if not self.auto_mode else "[MOTOR_SKIP]"
                self.log(f"{skip_tag} reason=paused")
                self._motor_debug_frames -= 1
            return False

        total_px = max(1, self.total_height)
        diff = self.target_progress - self.progress
        diff_px = diff * total_px
        dt = max(0.001, min(0.050, float(dt)))

        vel = getattr(self, "_scroll_vel_px", 0.0)
        velocity_before = vel
        accel = diff_px * 6.5
        vel = (vel + accel * dt) * 0.82

        line_h = max(1, self.line_height)
        max_px = max(1.2, min(6.0, line_h * 0.055))
        if abs(diff_px) > line_h * 1.5:
            max_px = max(2.0, min(8.0, line_h * 0.075))

        step_px = vel * dt
        if step_px > max_px:
            step_px = max_px
            vel = step_px / dt
        elif step_px < -max_px:
            step_px = -max_px
            vel = step_px / dt

        previous = self.progress
        if abs(diff_px) < 0.35 and abs(vel) < 6.0:
            self.progress = self.target_progress
            vel = 0.0
        else:
            next_progress = self.progress + (step_px / total_px)
            if self.auto_mode or self.target_progress >= previous:
                self.progress = max(previous, min(self.target_progress, next_progress))
            else:
                self.progress = min(previous, max(self.target_progress, next_progress))
            self.progress = max(0.0, min(1.0, self.progress))

        self._scroll_vel_px = vel
        self._update_display_progress()
        if self.target_progress >= 1.0 and self.progress >= 1.0:
            self.progress = 1.0
            self.target_progress = 1.0
            self.pending_progress = 1.0
            self._scroll_vel_px = 0.0
            self.finished = True

        if debug_motor:
            step_tag = "[MOTOR_MANUAL_STEP]" if not self.auto_mode else "[MOTOR_STEP]"
            self.log(
                f"{step_tag} "
                f"dt={dt:.4f} delta_y={diff_px:.2f} acceleration={accel:.2f} "
                f"velocity_before={velocity_before:.3f} velocity_after={vel:.3f} "
                f"step_px={step_px:.3f} current_y_before={previous * visual_height:.2f} "
                f"current_y_after={self.progress * visual_height:.2f} "
                f"target_y={self.target_progress * visual_height:.2f}"
            )
            self._motor_debug_frames -= 1

        return self.progress != previous

    def manual_step(self, delta: float):
        """Movimento manual independente do AUTO/IA.

        No teleprompter profissional, MANUAL não pode travar comandos.
        Cada toque/hold de Avançar/Voltar movimenta o TP mesmo com a IA pausada.
        """
        if self.auto_mode:
            self.log("Comando manual ignorado em AUTO; selecione MANUAL para Avançar/Voltar.")
            return False
        delta = max(-0.020, min(0.020, float(delta)))
        line_count = max(1, len(self.lines) - 1)
        line_before = self.target_line_index
        if line_before is None:
            line_before = int(round(self.progress * line_count))
        direction = "forward" if delta > 0 else "back"
        line_after = max(0, min(line_count, line_before + (1 if delta > 0 else -1)))
        target_before = self.target_progress * self._final_target_y()
        self.target_line_index = line_after
        self.target_progress = line_after / line_count
        self.finished = line_after >= line_count
        target_after = self.target_progress * self._final_target_y()
        # Instrumenta os frames seguintes para confirmar a perseguição física
        # do alvo manual sem alterar a dinâmica do motor.
        self._motor_debug_frames = max(getattr(self, "_motor_debug_frames", 0), 6)
        self.log(
            f"MANUAL_STEP direction={direction} line_before={line_before} "
            f"line_after={line_after} target_y_before={target_before:.1f} "
            f"target_y_after={target_after:.1f}"
        )
        self._last_draw_progress = -1.0
        self.draw()
        return True


    def toggle_play(self):
        self.playing = not self.playing

    def toggle_mirror(self):
        self.mirror = not self.mirror
        # Invalida todos os caches para forçar redesenho imediato.
        self._last_draw_progress = -1.0
        self._last_draw_size = (0, 0)
        self._mirror_pil_last_progress = -1.0
        self._mirror_pil_last_size = (0, 0)
        self.canvas.delete("all")
        self.draw()
        self.log("TP nativo: modo espelho alternado.")

    def _refresh_after_font_change(self):
        """Recalcula linhas e força redesenho imediato após mudar fonte."""
        try:
            current_progress = self.progress
            anchor = self._capture_position_anchor()

            self.lines = self.wrap_text_to_lines(self.raw_script_text)
            self.recalc_metrics()

            self._restore_position_anchor(anchor, current_progress)
            if self.last_word_index is not None:
                self._apply_word_target(self.last_word_index, self.last_word_count)
            self.last_accepted_progress = self.target_progress

            # Invalida cache do Canvas/PIL para a mudança aparecer na hora.
            self._last_draw_progress = -1.0
            self._last_draw_size = (0, 0)
            self._mirror_pil_last_progress = -1.0
            self._mirror_pil_last_size = (0, 0)

            self.canvas.delete("all")
            self.draw()
        except Exception as e:
            self.log(f"Erro ao atualizar fonte do TP: {e}")

    def font_plus(self):
        """Aumenta fonte do TP nativo.

        Faixa broadcast profissional:
        - 44 a 64 para uso normal em emissora;
        - acima de 64 só para apresentadores com visão reduzida.
        """
        self.font_size = min(64, int(self.font_size) + 4)
        self._refresh_after_font_change()
        self.log(f"TP nativo: fonte aumentada para {self.font_size}.")

    def font_minus(self):
        """Diminui fonte do TP nativo."""
        self.font_size = max(36, int(self.font_size) - 4)
        self._refresh_after_font_change()
        self.log(f"TP nativo: fonte reduzida para {self.font_size}.")


    def toggle_fullscreen(self, event=None):
        """Alterna fullscreen controlado por geometria do monitor.

        Correção:
        - evita chamada duplicada do F11;
        - retorna "break" para impedir propagação do evento;
        - mantém o fullscreen no monitor atual.
        """
        if self.fullscreen:
            return self.exit_fullscreen(event)

        try:
            self.normal_geometry = self.geometry()
            m = self._current_monitor()
            self.fullscreen = True

            self.overrideredirect(True)
            self.geometry(f'{m["width"]}x{m["height"]}+{m["left"]}+{m["top"]}')
            self.lift()
            self.focus_force()
            self.canvas.focus_set()
            self.log("TP nativo entrou em tela cheia no monitor atual.")
        except Exception as e:
            self.log(f"Erro ao entrar em tela cheia: {e}")

        return "break"

    def exit_fullscreen(self, event=None):
        """Sai da tela cheia de forma forçada."""
        try:
            if not self.fullscreen:
                self.overrideredirect(False)
                return "break"

            self.fullscreen = False
            self.overrideredirect(False)

            if self.normal_geometry:
                self.geometry(self.normal_geometry)
            else:
                self._place_on_monitor(self.current_monitor_index, fullscreen=False)

            self.lift()
            self.focus_force()
            self.canvas.focus_set()
            self.log("TP nativo saiu da tela cheia.")
        except Exception as e:
            self.log(f"Erro ao sair da tela cheia: {e}")

        return "break"

    def force_monitor_1(self):
        self.move_to_monitor(0)

    def force_monitor_2(self):
        self.move_to_monitor(1)

    # ── Render ───────────────────────────────────────────────────────

    def render_loop(self):
        self.process_messages()

        pending_trace = getattr(self, "_pending_mode_trace", None)
        if pending_trace:
            mode, trace = pending_trace
            self._pending_mode_trace = None
            now = time.perf_counter()
            base = trace.get("t_click", trace.get("t_native_receive", now))
            state_at = trace.get("t_native_state", base)
            self.log(
                f"TRACE {mode.upper()} primeiro render: "
                f"state→render={(now - state_at) * 1000:.2f} ms; "
                f"click→render={(now - base) * 1000:.2f} ms"
            )

        # Warm-up inicial após abrir/trocar roteiro.
        if time.time() < getattr(self, "warmup_until", 0.0):
            self.warmup_frames += 1
            self.progress = 0.0
            self.target_progress = 0.0
            self.display_progress = 0.0
            self.draw()
            self.after(self.render_interval_ms, self.render_loop)
            return

        # PAUSE congela o motor. Comandos manuais preservam o alvo e retomam
        # suavemente quando PLAY for acionado.
        if not self.playing:
            self.draw()
            self.after(self.render_interval_ms, self.render_loop)
            return

        # Em MANUAL, o TP não anda sozinho. Ele só se move por Avançar/Voltar/Shuttle.
        # A IA fica pausada no main_align_ws.py, mas a tela e os comandos seguem ativos.

        now = time.time()
        dt = max(0.001, min(0.050, now - getattr(self, "_last_frame_ts", now)))
        self._last_frame_ts = now
        self._advance_scroll_state(dt)
        if self._pending_pipe_motor_log is not None:
            visual_height = max(1, max(0, len(self.lines) - 1) * self.line_height)
            self.log(
                f"[PIPE 8 #{self._pending_pipe_motor_log}] MOTOR "
                f"current_y={self.progress * visual_height:.1f} "
                f"target_y={self.target_progress * visual_height:.1f} "
                f"auto={self.auto_mode} playing={self.playing} finished={self.finished}"
            )
            self._pending_pipe_motor_log = None
        self._log_scroll_tracking()

        self.draw()
        self.after(self.render_interval_ms, self.render_loop)


    def _font_tuple(self):
        return (self.font_family, self.font_size, "bold")

    def _pil_font(self):
        if not PIL_NATIVE_MIRROR_OK:
            return None
        try:
            # Windows comum
            return PILImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", self.font_size)
        except Exception:
            try:
                return PILImageFont.truetype("arialbd.ttf", self.font_size)
            except Exception:
                return PILImageFont.load_default()

    def _scene_positions(self, w: int, h: int):
        # Guia no topo: apresentador lê a linha na guia e tem toda a tela branca abaixo.
        guide_y = int(h * 0.30)
        self.recalc_metrics()

        active_index = self.progress * max(1, len(self.lines) - 1)
        active_y = active_index * self.line_height
        offset_y = guide_y - active_y - (self.line_height * self.active_line_offset_lines)

        return guide_y, offset_y

    def draw(self):
        c = self.canvas
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())

        # Otimização CPU:
        # Se o progresso praticamente não mudou e o tamanho da janela é igual,
        # evita redesenho completo do Canvas.
        if not self.mirror:
            size_key = (w, h)
            if size_key == getattr(self, "_last_draw_size", (0, 0)):
                mode_unchanged = self.auto_mode == getattr(self, "_last_draw_auto_mode", None)
                if mode_unchanged and abs(self.progress - getattr(self, "_last_draw_progress", -1.0)) < 0.00025:
                    return
            self._last_draw_size = size_key
            self._last_draw_progress = self.progress
            self._last_draw_auto_mode = self.auto_mode

        if self.mirror and PIL_NATIVE_MIRROR_OK:
            self.draw_mirror_pil(w, h)
            return

        c.delete("all")
        guide_y, offset_y = self._scene_positions(w, h)

        # ── Fundo preto total ──────────────────────────────────────────
        c.create_rectangle(0, 0, w, h, fill="black", outline="black")

        # ── Linha guia broadcast (estilo Autocue/QTV) ─────────────────
        # Faixa semitransparente (simulada com retângulo) na zona de leitura
        c.create_rectangle(0, guide_y - 4, w, guide_y + 4,
                           fill="#ffdd00", outline="")
        # Marcador triangular lateral
        c.create_polygon(
            8,  guide_y - 20,
            8,  guide_y + 20,
            40, guide_y,
            fill="#ffdd00",
            outline=""
        )

        # ── HUD compacto no canto superior direito ─────────────────────
        mode_text = "AUTO" if self.auto_mode else "MANUAL"
        display_percent = int(self.display_progress * 100)
        hud_text = f"{mode_text} ► {display_percent}%"
        hud_color = "#ffdd00" if self.auto_mode else "#38bdf8"
        c.create_text(
            w - 16, 16,
            text=hud_text,
            fill=hud_color,
            font=("Arial", 13, "bold"),
            anchor="ne"
        )
        self.log(
            f'HUD_RENDER auto_mode={self.auto_mode} mode_text={mode_text} '
            f'display_progress={self.display_progress:.3f} text="{hud_text}"'
        )
        if self.mirror:
            c.create_text(16, 16, text="⟺ ESPELHO",
                          fill="#c084fc", font=("Arial", 13, "bold"), anchor="nw")

        # ── Texto ──────────────────────────────────────────────────────
        # Padrão broadcast:
        #   - Linhas acima da guia (já lidas): cinza escuro
        #   - Linha na guia (lendo agora):     branco puro + levemente maior
        #   - Linhas abaixo da guia (a ler):   branco puro
        x = self.side_margin if getattr(self, "text_align_left", True) else w // 2
        text_anchor = "w" if getattr(self, "text_align_left", True) else "center"
        text_justify = "left" if getattr(self, "text_align_left", True) else "center"
        font = self._font_tuple()
        # Fonte ligeiramente menor para as linhas já lidas
        font_read = (self.font_family, max(32, self.font_size - 4), "bold")

        visible_margin = self.line_height * 3
        first_i = max(0, int((-offset_y - visible_margin) / self.line_height))
        last_i = min(len(self.lines), int((h - offset_y + visible_margin) / self.line_height) + 1)

        for i in range(first_i, last_i):
            line = self.lines[i]
            y = offset_y + (i * self.line_height)

            if not line:
                continue

            dist = abs(y - guide_y)
            on_guide = dist < self.line_height * 1.0
            above_guide = y < guide_y - self.line_height * 0.5

            if above_guide:
                # Já lida — cinza escuro, opção de desaparecer gradualmente
                # Quanto mais longe da guia, mais escuro.
                darkness = min(1.0, dist / (self.line_height * 5))
                gray_val = max(30, int(90 - darkness * 60))
                fill = f"#{gray_val:02x}{gray_val:02x}{gray_val:02x}"
                draw_font = font_read
            elif on_guide:
                # Linha sendo lida agora — branco puro, destaque máximo
                fill = "#ffffff"
                draw_font = font
            else:
                # A ser lida — branco puro
                fill = "#ffffff"
                draw_font = font

            # Fallback quando PIL não está instalado: inverte a ordem dos caracteres.
            draw_line = line[::-1] if self.mirror else line

            c.create_text(
                x,
                y,
                text=draw_line,
                fill=fill,
                font=draw_font,
                anchor=text_anchor,
                justify=text_justify,
                width=max(300, w - (self.side_margin * 2))
            )

    def draw_mirror_pil(self, w: int, h: int):
        """Renderiza a cena em Pillow e espelha horizontalmente.

        Isso dá espelhamento real para uso em vidro/teleprompter.
        """
        c = self.canvas
        c.delete("all")

        img = PILImage.new("RGB", (w, h), "black")
        d = PILImageDraw.Draw(img)

        guide_y, offset_y = self._scene_positions(w, h)

        # Linha guia broadcast (faixa amarela + marcador triangular)
        d.rectangle([(0, guide_y - 4), (w, guide_y + 4)], fill="#ffdd00")
        d.polygon([(8, guide_y - 20), (8, guide_y + 20), (40, guide_y)], fill="#ffdd00")

        mode_text = "AUTO" if self.auto_mode else "MANUAL"
        display_percent = int(self.display_progress * 100)
        hud_text = f"{mode_text} ► {display_percent}%"
        small_font = None
        try:
            small_font = PILImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 18)
        except Exception:
            small_font = PILImageFont.load_default()

        hud_color = "#ffdd00" if self.auto_mode else "#38bdf8"
        d.text((w - 16, 16), hud_text,
               fill=hud_color, font=small_font, anchor="rt" if hasattr(small_font, 'getbbox') else None)
        self.log(
            f'HUD_RENDER auto_mode={self.auto_mode} mode_text={mode_text} '
            f'display_progress={self.display_progress:.3f} text="{hud_text}"'
        )
        d.text((40, 18), "⟺ ESPELHO", fill="#c084fc", font=small_font)

        font = self._pil_font()

        # Para o espelhamento real, desenha na direita antes do flip,
        # assim o resultado final aparece alinhado à esquerda.
        x = w - self.side_margin if getattr(self, "text_align_left", True) else w // 2

        # Otimização para espelhamento: desenha só as linhas visíveis.
        visible_margin = self.line_height * 3
        first_i = max(0, int((-offset_y - visible_margin) / self.line_height))
        last_i = min(len(self.lines), int((h - offset_y + visible_margin) / self.line_height) + 1)

        for i in range(first_i, last_i):
            line = self.lines[i]
            y = int(offset_y + (i * self.line_height))

            if not line:
                continue

            dist = abs(y - guide_y)
            on_guide = dist < self.line_height * 1.0
            above_guide = y < guide_y - self.line_height * 0.5

            if above_guide:
                # Já lida — cinza escuro com degradê
                darkness = min(1.0, dist / (self.line_height * 5))
                gray_val = max(30, int(90 - darkness * 60))
                fill = (gray_val, gray_val, gray_val)
            elif on_guide:
                fill = (255, 255, 255)
            else:
                fill = (255, 255, 255)

            try:
                bbox = d.textbbox((0, 0), line, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except Exception:
                tw = len(line) * self.font_size * 0.5
                th = self.font_size

            if getattr(self, "text_align_left", True):
                d.text((x - tw, y - th / 2), line, fill=fill, font=font)
            else:
                d.text((x - tw / 2, y - th / 2), line, fill=fill, font=font)

        # Espelha toda a cena
        img = img.transpose(PILImage.FLIP_LEFT_RIGHT)
        photo = PILImageTk.PhotoImage(img)
        self.native_image_ref = photo
        c.create_image(0, 0, image=self.native_image_ref, anchor="nw")

    def close(self):
        self.ws_stop.set()
        try:
            self.destroy()
        except Exception:
            pass



# ── Aplicação principal ────────────────────────────────────────────────

class TPControlGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        apply_soft_broadcast_theme(self)
        apply_status_styles(self)
        self.geometry("1200x820")
        self.minsize(1000, 700)

        self.project_dir = Path.cwd()
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.ws_proc   = ManagedProcess("WebSocket", "scroll_server.py")
        self.ai_proc   = ManagedProcess("IA / Voz",  "main_align_ws.py")

        self.http_server: ThreadingHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.http_running = False
        self.lan_access_enabled = ALLOW_LAN
        self._http_bind_host = None
        self._ws_lan_enabled = None
        self._lan_enable_error = None

        # Variáveis de estado
        python_path, python_source = self._resolve_python_cmd()
        self.python_cmd          = tk.StringVar(value=python_path)
        self._python_config_source = python_source
        self._log_python_config(python_path, python_source)
        self.device_var          = tk.StringVar(value="")
        self.status_ws           = tk.StringVar(value="Parado")
        self.status_http         = tk.StringVar(value="Parado")
        self.status_ai           = tk.StringVar(value="Parado")
        self.status_tp_conn      = tk.StringVar(value="TP: desconectado")
        self.status_remote_conn  = tk.StringVar(value="Remoto: N/D")
        self.status_summary      = tk.StringVar(value="Sistema parado")
        self.mode_status_var     = tk.StringVar(value="MODO: AUTO")
        self.project_var         = tk.StringVar(value=str(self.project_dir))
        self.manual_speed_var    = tk.DoubleVar(value=0.0012)
        self.manual_speed_label_var = tk.StringVar(value="Velocidade: 0.0012")
        self.roteiro_selected_var   = tk.StringVar(value="")
        self.remote_pin_var          = tk.StringVar(value=str(random.randint(1000, 9999)))

        # Hold (botões segurar)
        self.hold_action = None
        self.hold_job    = None

        # Hold remoto integrado
        self._remote_hold_job = None

        # Janela TP nativa flutuante
        self.tp_native_window = None
        self._last_ai_reset_ts = 0.0

        # Sender persistente da GUI. Evita criar um processo Python e uma
        # conexão WebSocket nova para cada comando de operação.
        self._control_ws_queue: queue.Queue[dict | None] = queue.Queue()
        self._control_ws_stop = threading.Event()
        self._control_ws_thread = threading.Thread(
            target=self._control_ws_thread_main,
            daemon=True,
            name="GUIControlWebSocket",
        )
        if WEBSOCKETS_OK:
            self._control_ws_thread.start()

        self._build_ui()
        self._load_project_files()
        self.after(500, self._drain_log_queue)
        self.after(1000, self._refresh_status_loop)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ══════════════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # ── Barra superior: pasta do projeto ──────────────────────────
        top = ttk.Frame(self, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Pasta do projeto:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.project_var).grid(
            row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Selecionar",
                   command=self.select_project_dir).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Recarregar arquivos",
                   command=self._load_project_files).grid(row=0, column=3, padx=4)

        # ── Configuração ──────────────────────────────────────────────
        config = ttk.LabelFrame(self, text="Configuração", padding=10)
        config.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        config.columnconfigure(7, weight=1)

        ttk.Label(config, text="Python:").grid(row=0, column=0, sticky="w")
        ttk.Entry(config, textvariable=self.python_cmd, width=18).grid(
            row=0, column=1, padx=(4, 12), sticky="w")
        ttk.Button(config, text="Testar Python",
                   command=self.test_python).grid(row=0, column=2, padx=4)
        ttk.Label(config, text="Device áudio:").grid(
            row=0, column=3, sticky="w", padx=(18, 4))
        ttk.Entry(config, textvariable=self.device_var, width=8).grid(
            row=0, column=4, sticky="w")
        ttk.Button(config, text="Salvar Device no main_align",
                   command=self.save_device_to_main).grid(row=0, column=5, padx=4)
        ttk.Button(config, text="Listar mics",
                   command=self.list_mics).grid(row=0, column=6, padx=4)

        # ── Notebook principal ────────────────────────────────────────
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

        # Tab 1: Painel de controle
        self._tab_control = ttk.Frame(self.notebook)
        self.notebook.add(self._tab_control, text="  🎛  Painel de Controle  ")
        self._build_tab_control(self._tab_control)

        # Tab 2: TP embutido
        self._tab_tp = ttk.Frame(self.notebook)
        self.notebook.add(self._tab_tp, text="  📺  TP Nativo  ")
        self._build_tab_tp(self._tab_tp)

        # Tab 3: Remoto integrado
        self._tab_remote = ttk.Frame(self.notebook)
        self.notebook.add(self._tab_remote, text="  📱  Controle Remoto  ")
        self._build_tab_remote(self._tab_remote)

        # Tab 4: Acesso Celular (IP + QR Code)
        self._tab_qr = ttk.Frame(self.notebook)
        self.notebook.add(self._tab_qr, text="  📡  Pareamento BY / Remoto  ")
        self._build_tab_qr(self._tab_qr)
        self.notebook.bind(
            "<<NotebookTabChanged>>", self._on_notebook_tab_changed, add="+"
        )

    # ── Tab: Painel de Controle ────────────────────────────────────────

    def _build_tab_control(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(3, weight=1)

        # Botões principais
        controls = ttk.LabelFrame(parent, text="Controle da aplicação", padding=6)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 4))
        for c in range(7):
            controls.columnconfigure(c, weight=1)

        ttk.Button(controls, text="▶ Iniciar Tudo",
                   command=self.start_all, style="Green.TButton").grid(
            row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="■ Parar Tudo",
                   command=self.stop_all, style="Red.TButton").grid(
            row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="📺 Abrir TP Nativo",
                   command=self.open_tp_native_window, style="Blue.TButton").grid(
            row=0, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="🪞 Espelhar TP Nativo",
                   command=self.toggle_native_mirror_safe, style="Purple.TButton").grid(
            row=0, column=3, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="📱 Controle Remoto",
                   command=self.open_remote, style="Orange.TButton").grid(
            row=0, column=4, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="📷 QR Celular",
                   command=self.open_remote_qr, style="Orange.TButton").grid(
            row=0, column=5, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Limpar Log",
                   command=self.clear_log).grid(
            row=0, column=6, sticky="ew", padx=3, pady=3)

        ttk.Button(controls, text="Iniciar WebSocket",
                   command=self.start_ws).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Parar WebSocket",
                   command=self.stop_ws).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Label(controls, textvariable=self.status_ws).grid(
            row=1, column=2, sticky="w", padx=8)

        ttk.Button(controls, text="Iniciar HTTP",
                   command=self.start_http).grid(row=2, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Parar HTTP",
                   command=self.stop_http).grid(row=2, column=1, sticky="ew", padx=3, pady=3)
        ttk.Label(controls, textvariable=self.status_http).grid(
            row=2, column=2, sticky="w", padx=8)

        ttk.Button(controls, text="Iniciar IA/Voz",
                   command=self.start_ai).grid(row=3, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Parar IA/Voz",
                   command=self.stop_ai).grid(row=3, column=1, sticky="ew", padx=3, pady=3)
        ttk.Label(controls, textvariable=self.status_ai).grid(
            row=3, column=2, sticky="w", padx=8)

        # Modo operador
        self.btn_mode_auto = ttk.Button(
            controls, text="AUTO", command=self.tp_auto, style="Green.TButton")
        self.btn_mode_auto.grid(row=4, column=0, sticky="ew", padx=3, pady=3)
        self.btn_mode_manual = ttk.Button(
            controls, text="MANUAL", command=self.tp_manual, style="TButton")
        self.btn_mode_manual.grid(row=4, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="PLAY/PAUSE",
                   command=self.tp_play_pause, style="Blue.TButton").grid(
            row=4, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Reset TP",
                   command=self.tp_reset, style="Red.TButton").grid(
            row=4, column=3, sticky="ew", padx=3, pady=3)
        ttk.Label(
            controls, textvariable=self.mode_status_var, font=("Arial", 11, "bold")
        ).grid(row=4, column=4, columnspan=2, sticky="w", padx=8)

        ttk.Label(controls, text="Velocidade manual:").grid(
            row=5, column=0, sticky="w", padx=3, pady=3)
        speed = ttk.Scale(controls, from_=0.0003, to=0.0030,
                          variable=self.manual_speed_var, orient="horizontal",
                          command=lambda _v: self.tp_set_speed())
        speed.grid(row=5, column=1, columnspan=3, sticky="ew", padx=3, pady=3)
        ttk.Label(controls, textvariable=self.manual_speed_label_var).grid(
            row=5, column=4, sticky="w", padx=3, pady=3)

        ttk.Button(controls, text="Lento",
                   command=self.tp_speed_slow).grid(row=6, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Normal",
                   command=self.tp_speed_normal).grid(row=6, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="Rápido",
                   command=self.tp_speed_fast).grid(row=6, column=3, sticky="ew", padx=3, pady=3)

        btn_back = ttk.Button(controls, text="◀ Voltar")
        btn_back.grid(row=6, column=4, sticky="ew", padx=3, pady=3)
        btn_back.bind("<ButtonPress-1>",   lambda _e: self.tp_hold_start("back"))
        btn_back.bind("<ButtonRelease-1>", lambda _e: self.tp_hold_stop())

        btn_forward = ttk.Button(controls, text="Avançar ▶")
        btn_forward.grid(row=6, column=5, sticky="ew", padx=3, pady=3)
        btn_forward.bind("<ButtonPress-1>",   lambda _e: self.tp_hold_start("forward"))
        btn_forward.bind("<ButtonRelease-1>", lambda _e: self.tp_hold_stop())

        # Status broadcast
        status_frame = ttk.LabelFrame(parent, text="Status broadcast", padding=5)
        status_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 4))
        for c in range(6):
            status_frame.columnconfigure(c, weight=1)

        self.status_ws_label = ttk.Label(status_frame, textvariable=self.status_ws,
                                         style="StatusNeutral.TLabel")
        self.status_ws_label.grid(row=0, column=0, sticky="ew", padx=3, pady=2)
        self.status_http_label = ttk.Label(status_frame, textvariable=self.status_http,
                                           style="StatusNeutral.TLabel")
        self.status_http_label.grid(row=0, column=1, sticky="ew", padx=3, pady=2)
        self.status_ai_label = ttk.Label(status_frame, textvariable=self.status_ai,
                                         style="StatusNeutral.TLabel")
        self.status_ai_label.grid(row=0, column=2, sticky="ew", padx=3, pady=2)
        self.status_tp_label = ttk.Label(status_frame, textvariable=self.status_tp_conn,
                                         style="StatusNeutral.TLabel")
        self.status_tp_label.grid(row=0, column=3, sticky="ew", padx=3, pady=2)
        self.status_remote_label = ttk.Label(status_frame, textvariable=self.status_remote_conn,
                                             style="StatusNeutral.TLabel")
        self.status_remote_label.grid(row=0, column=4, sticky="ew", padx=3, pady=2)
        self.status_summary_label = ttk.Label(status_frame, textvariable=self.status_summary,
                                              style="StatusNeutral.TLabel")
        self.status_summary_label.grid(row=0, column=5, sticky="ew", padx=3, pady=2)

        # Biblioteca de roteiros
        roteiros_mgr = ttk.LabelFrame(parent, text="Biblioteca de roteiros", padding=5)
        roteiros_mgr.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 4))
        roteiros_mgr.columnconfigure(1, weight=1)

        ttk.Label(roteiros_mgr, text="Roteiro:").grid(row=0, column=0, sticky="w")
        self.roteiro_combo = ttk.Combobox(roteiros_mgr, textvariable=self.roteiro_selected_var,
                                          state="readonly", width=34)
        self.roteiro_combo.grid(row=0, column=1, sticky="ew", padx=6)
        self.roteiro_combo.bind("<<ComboboxSelected>>",
                                lambda _e: self.preview_selected_roteiro())
        ttk.Button(roteiros_mgr, text="Abrir",
                   command=self.open_selected_roteiro).grid(row=0, column=2, padx=3)
        ttk.Button(roteiros_mgr, text="Atualizar",
                   command=self.refresh_roteiros_list).grid(row=0, column=3, padx=3)
        ttk.Button(roteiros_mgr, text="Novo",
                   command=self.new_roteiro).grid(row=1, column=0, padx=3, pady=(6, 0), sticky="ew")
        ttk.Button(roteiros_mgr, text="Salvar Como",
                   command=self.save_roteiro_as).grid(row=1, column=1, padx=3, pady=(6, 0), sticky="w")
        ttk.Button(roteiros_mgr, text="Excluir",
                   command=self.delete_selected_roteiro).grid(row=1, column=2, padx=3, pady=(6, 0), sticky="ew")
        ttk.Button(roteiros_mgr, text="Abrir pasta",
                   command=self.open_roteiros_folder).grid(row=1, column=3, padx=3, pady=(6, 0), sticky="ew")

        # Painel dividido: roteiro + log
        paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=4, pady=(0, 4))

        left  = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left,  weight=1)
        paned.add(right, weight=1)

        roteiro_frame = ttk.LabelFrame(left, text="Roteiro ativo (roteiro.txt)", padding=5)
        roteiro_frame.pack(fill="both", expand=True)
        roteiro_frame.rowconfigure(0, weight=1)
        roteiro_frame.columnconfigure(0, weight=1)
        self.roteiro_text = ScrolledText(roteiro_frame, wrap="word",
                                         font=("Consolas", 11), height=14)
        self.roteiro_text.grid(row=0, column=0, sticky="nsew")
        rbtns = ttk.Frame(roteiro_frame)
        rbtns.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(rbtns, text="Salvar roteiro ativo",
                   command=self.save_roteiro).pack(side="left", padx=4)
        ttk.Button(rbtns, text="Recarregar ativo",
                   command=self.reload_active_and_tp).pack(side="left", padx=4)
        ttk.Button(rbtns, text="Abrir pasta projeto",
                   command=self.open_folder).pack(side="left", padx=4)

        log_frame = ttk.LabelFrame(right, text="Log", padding=8)
        log_frame.pack(fill="both", expand=True)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(log_frame, wrap="word", font=("Consolas", 10),
                                     height=20, bg="#111827", fg="#dbeafe")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        help_frame = ttk.LabelFrame(right, text="Como usar", padding=8)
        help_frame.pack(fill="x", pady=(8, 0))
        help_txt = (
            "1) Confira a pasta do projeto.\n"
            "2) Clique em Iniciar Tudo.\n"
            "3) 📺 TP na janela → abre o teleprompter DENTRO desta janela (sem browser).\n"
            "4) 📱 Aba 'Controle Remoto' → painel de controle sem browser externo.\n"
            "5) TP Nativo é a tela principal; browser fica apenas como contingência técnica.\n"
            "6) Use AUTO/MANUAL/PLAY para backup de operador."
        )
        ttk.Label(help_frame, text=help_txt, justify="left").pack(anchor="w")

    # ── Tab: Teleprompter embutido ─────────────────────────────────────

    def _build_tab_tp(self, parent):
        """Aba de orientação do TP nativo."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        box = ttk.Frame(parent, padding=28)
        box.grid(row=0, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)

        ttk.Label(
            box,
            text="Teleprompter Nativo — Canvas",
            font=("Segoe UI Semibold", 22)
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        ttk.Label(
            box,
            text=(
                "Esta versão elimina o browser/tkinterweb da tela principal do TP.\\n\\n"
                "O texto é renderizado diretamente em um Canvas Tkinter, em uma janela Toplevel independente.\\n\\n"
                "Como usar:\\n"
                "1) Clique em 'Abrir TP Nativo'.\\n"
                "2) Arraste a janela para o segundo monitor.\\n"
                "3) Pressione F11 para tela cheia.\\n"
                "4) Pressione ESC para sair da tela cheia.\\n"
            ),
            font=("Segoe UI", 12),
            justify="left",
            foreground="#374151"
        ).grid(row=1, column=0, sticky="w", pady=(0, 18))

        actions = ttk.Frame(box)
        actions.grid(row=2, column=0, sticky="w")

        ttk.Button(
            actions,
            text="📺 Abrir TP Nativo",
            command=self.open_tp_native_window,
            style="Blue.TButton"
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            actions,
            text="🔄 Recarregar roteiro",
            command=self.reload_active_and_tp,
            style="Orange.TButton"
        ).pack(side="left", padx=8)

        ttk.Button(
            actions,
            text="🪞 Espelhar",
            command=self.toggle_native_mirror_safe,
            style="Purple.TButton"
        ).pack(side="left", padx=8)

        ttk.Button(
            actions,
            text="Monitor 1",
            command=lambda: self.move_native_to_monitor(0),
            style="Blue.TButton"
        ).pack(side="left", padx=8)

        ttk.Button(
            actions,
            text="Monitor 2",
            command=lambda: self.move_native_to_monitor(1),
            style="Blue.TButton"
        ).pack(side="left", padx=8)

    def move_native_to_monitor(self, index: int):
        """Move o TP nativo para o monitor desejado."""
        if self.tp_native_window is None or not self.tp_native_window.winfo_exists():
            self.open_tp_native_window()

        try:
            self.tp_native_window.move_to_monitor(index)
        except Exception as e:
            self.log(f"Erro ao mover TP para monitor {index + 1}: {e}")

    def open_tp_native_window(self):
        """Abre o TP nativo em janela flutuante independente."""
        if self.tp_native_window is not None:
            try:
                if self.tp_native_window.winfo_exists():
                    self.tp_native_window.deiconify()
                    self.tp_native_window.lift()
                    self.tp_native_window.focus_force()
                    self.log("TP nativo já estava aberto. Janela trazida para frente.")
                    return
            except Exception:
                self.tp_native_window = None

        self.tp_native_window = NativeTPWindow(self, self._project_dir, self.log)
        self.log("TP nativo aberto em janela flutuante.")

    def toggle_native_mirror_safe(self):
        """Alterna espelhamento sem bloquear a interface."""
        try:
            if self.tp_native_window is None or not self.tp_native_window.winfo_exists():
                self.open_tp_native_window()
            self.after(10, self._do_toggle_native_mirror)
        except Exception as e:
            self.log(f"Erro ao solicitar espelhamento: {e}")

    def _do_toggle_native_mirror(self):
        try:
            native_window = self.tp_native_window
            if native_window is None or not native_window.winfo_exists():
                return
            if hasattr(native_window, "toggle_mirror"):
                native_window.toggle_mirror()
            elif hasattr(native_window, "mirror"):
                native_window.mirror = not native_window.mirror
                native_window.draw()
            self.log("Espelhamento alternado.")
        except Exception as e:
            self.log(f"Erro ao alternar espelhamento: {e}")

    def reload_tp_native_window(self):
        """Recarrega o roteiro no TP nativo."""
        if self.tp_native_window is None or not self.tp_native_window.winfo_exists():
            self.open_tp_native_window()
            return

        self.tp_native_window.reload_script()
        self.log("TP nativo recarregado.")

    def close_tp_native_window(self):
        """Fecha a janela nativa do TP."""
        try:
            if self.tp_native_window is not None and self.tp_native_window.winfo_exists():
                self.tp_native_window.close()
        except Exception:
            pass
        self.tp_native_window = None

    # Compatibilidade com nomes antigos.
    def _tp_embedded_load(self):
        self.open_tp_native_window()

    def _tp_embedded_reload(self):
        self.reload_tp_native_window()

    def _show_tp_tab(self):
        self.open_tp_native_window()

    def open_tp_floating_window(self):
        self.open_tp_native_window()

    def reload_tp_floating_window(self):
        self.reload_tp_native_window()

    def close_tp_floating_window(self):
        self.close_tp_native_window()

    # ── Tab: Controle Remoto integrado ─────────────────────────────────

    def _build_tab_remote(self, parent):
        """
        Painel de controle idêntico ao remote.html, mas dentro da GUI.
        Envia comandos via WebSocket da mesma forma que o HTML faz.
        """
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        # Container central com largura máxima
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True, padx=30, pady=20)
        outer.columnconfigure(0, weight=1)

        # Título e status
        ttk.Label(outer, text="Controle Remoto Integrado",
                  font=("Segoe UI Semibold", 18)).grid(
            row=0, column=0, sticky="w", pady=(0, 4))

        self._remote_status_var = tk.StringVar(value="WebSocket: aguardando...")
        status_lbl = ttk.Label(outer, textvariable=self._remote_status_var,
                               font=("Segoe UI", 11))
        status_lbl.grid(row=1, column=0, sticky="w", pady=(0, 12))

        # ── Velocidade ──────────────────────────────────────────────────
        spd_frame = ttk.LabelFrame(outer, text="Velocidade manual", padding=10)
        spd_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        spd_frame.columnconfigure(1, weight=1)

        self._remote_speed_var       = tk.DoubleVar(value=0.0012)
        self._remote_speed_label_var = tk.StringVar(value="0.0012")

        ttk.Label(spd_frame, textvariable=self._remote_speed_label_var,
                  font=("Segoe UI Semibold", 20)).grid(
            row=0, column=0, columnspan=3, pady=(0, 6))

        speed_scale = ttk.Scale(spd_frame, from_=0.0003, to=0.0030,
                                variable=self._remote_speed_var,
                                orient="horizontal",
                                command=lambda _v: self._remote_update_speed())
        speed_scale.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        preset_frame = ttk.Frame(spd_frame)
        preset_frame.grid(row=2, column=0, columnspan=3, sticky="ew")
        for c in range(3):
            preset_frame.columnconfigure(c, weight=1)

        ttk.Button(preset_frame, text="Lento",
                   command=lambda: self._remote_set_preset(0.0006)).grid(
            row=0, column=0, sticky="ew", padx=3)
        ttk.Button(preset_frame, text="Normal",
                   command=lambda: self._remote_set_preset(0.0012)).grid(
            row=0, column=1, sticky="ew", padx=3)
        ttk.Button(preset_frame, text="Rápido",
                   command=lambda: self._remote_set_preset(0.0020)).grid(
            row=0, column=2, sticky="ew", padx=3)

        ttk.Label(spd_frame,
                  text="Use em modo MANUAL. Segure Avançar/Voltar para rolagem contínua.",
                  font=("Segoe UI", 10), foreground="#6b7280").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self._remote_last_var = tk.StringVar(value="Último comando: nenhum")
        ttk.Label(spd_frame, textvariable=self._remote_last_var,
                  font=("Consolas", 9), foreground="#9ca3af").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # ── Botões principais ───────────────────────────────────────────
        btn_grid = ttk.Frame(outer)
        btn_grid.grid(row=3, column=0, sticky="ew")
        for c in range(2):
            btn_grid.columnconfigure(c, weight=1)

        def big_btn(text, style, cmd, r, c, colspan=1):
            b = ttk.Button(btn_grid, text=text, style=style, command=cmd)
            b.grid(row=r, column=c, columnspan=colspan,
                   sticky="ew", padx=5, pady=5, ipady=6)
            return b

        big_btn("🤖  AUTO IA",      "RemoteGreen.TButton",  self._remote_auto,       0, 0)
        big_btn("✋  MANUAL",       "RemoteYellow.TButton", self._remote_manual,     0, 1)
        big_btn("⏯  PLAY / PAUSE", "RemotePrimary.TButton",self._remote_play_pause, 1, 0)
        big_btn("↩  RESET",         "RemoteRed.TButton",    self._remote_reset,      1, 1)

        # Botões de Voltar / Avançar com hold
        btn_back = ttk.Button(btn_grid, text="◀  Voltar", style="RemoteHold.TButton")
        btn_back.grid(row=2, column=0, sticky="ew", padx=5, pady=5, ipady=6)
        btn_back.bind("<ButtonPress-1>",
                      lambda _e: self._remote_hold_start("back", 0.006))
        btn_back.bind("<ButtonRelease-1>",
                      lambda _e: self._remote_hold_stop())
        btn_back.bind("<Leave>",
                      lambda _e: self._remote_hold_stop())

        btn_fwd = ttk.Button(btn_grid, text="Avançar  ▶", style="RemoteHold.TButton")
        btn_fwd.grid(row=2, column=1, sticky="ew", padx=5, pady=5, ipady=6)
        btn_fwd.bind("<ButtonPress-1>",
                     lambda _e: self._remote_hold_start("forward", 0.006))
        btn_fwd.bind("<ButtonRelease-1>",
                     lambda _e: self._remote_hold_stop())
        btn_fwd.bind("<Leave>",
                     lambda _e: self._remote_hold_stop())

        btn_back2 = ttk.Button(btn_grid, text="◀◀  Voltar +", style="RemoteHold.TButton")
        btn_back2.grid(row=3, column=0, sticky="ew", padx=5, pady=5, ipady=6)
        btn_back2.bind("<ButtonPress-1>",
                       lambda _e: self._remote_hold_start("back", 0.014))
        btn_back2.bind("<ButtonRelease-1>",
                       lambda _e: self._remote_hold_stop())
        btn_back2.bind("<Leave>",
                       lambda _e: self._remote_hold_stop())

        btn_fwd2 = ttk.Button(btn_grid, text="Avançar +  ▶▶", style="RemoteHold.TButton")
        btn_fwd2.grid(row=3, column=1, sticky="ew", padx=5, pady=5, ipady=6)
        btn_fwd2.bind("<ButtonPress-1>",
                      lambda _e: self._remote_hold_start("forward", 0.014))
        btn_fwd2.bind("<ButtonRelease-1>",
                      lambda _e: self._remote_hold_stop())
        btn_fwd2.bind("<Leave>",
                      lambda _e: self._remote_hold_stop())

        big_btn("A−  Fonte Menor",  "TButton", self._remote_font_minus, 4, 0)
        big_btn("A+  Fonte Maior",  "TButton", self._remote_font_plus,  4, 1)

        big_btn("🪞  Espelhar / Normal", "RemotePurple.TButton",
                self._remote_mirror, 5, 0, colspan=2)

    # ── Ações do Remoto integrado ──────────────────────────────────────

    def _remote_send(self, payload: dict):
        self._send_tp_command(payload)
        self._remote_last_var.set(f"Último: {json.dumps(payload, ensure_ascii=False)}")

    def _remote_auto(self):
        self._remote_send({"type": "control", "mode": "auto"})

    def _remote_manual(self):
        self._remote_send({"type": "control", "mode": "manual"})

    def _remote_play_pause(self):
        self._remote_send({"type": "control", "action": "toggle_play"})

    def _remote_reset(self):
        self._remote_send({"type": "control", "action": "reset"})

    def _remote_mirror(self):
        self.toggle_native_mirror_safe()

    def _remote_font_plus(self):
        self._remote_send({"type": "control", "action": "font_plus"})

    def _remote_font_minus(self):
        self._remote_send({"type": "control", "action": "font_minus"})

    def _remote_update_speed(self):
        v = max(0.0003, min(0.0030, float(self._remote_speed_var.get())))
        self._remote_speed_var.set(v)
        self._remote_speed_label_var.set(f"{v:.4f}")
        self._remote_send({"type": "control", "speed": v})

    def _remote_set_preset(self, value: float):
        self._remote_speed_var.set(value)
        self._remote_update_speed()

    def _remote_hold_start(self, action: str, value: float):
        self._remote_hold_stop()
        payload = {"type": "control", "action": action, "value": value}
        self._remote_send(payload)
        self._remote_hold_payload = payload
        self._remote_hold_tick()

    def _remote_hold_tick(self):
        if not hasattr(self, "_remote_hold_payload") or self._remote_hold_payload is None:
            return
        self._remote_send(self._remote_hold_payload)
        self._remote_hold_job = self.after(120, self._remote_hold_tick)

    def _remote_hold_stop(self):
        self._remote_hold_payload = None
        if self._remote_hold_job:
            try:
                self.after_cancel(self._remote_hold_job)
            except Exception:
                pass
            self._remote_hold_job = None

    # ── Tab: Acesso pelo Celular (IP + QR Code) ───────────────────────

    

    
    @staticmethod
    def _get_local_ip() -> str | None:
        try:
            stats = psutil.net_if_stats()
            interfaces = []
            for interface_name, addrs in psutil.net_if_addrs().items():
                ipv4_addresses = [
                    addr.address for addr in addrs if addr.family == socket.AF_INET
                ]
                is_up = bool(stats.get(interface_name) and stats[interface_name].isup)
                interfaces.append((interface_name, is_up, ipv4_addresses))
            return select_lan_ipv4(interfaces)
        except Exception as e:
            print("Erro ao detectar IPv4 LAN:", e)
            return None
    def _build_tab_qr(self, parent):
        """Aba de pareamento BY/Remoto."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(parent, padding=(10, 8))
        toolbar.grid(row=0, column=0, sticky="ew")

        ttk.Button(toolbar, text="🔄 Atualizar IP / QR Code",
                   command=self._qr_enable_and_refresh,
                   style="Blue.TButton").pack(side="left", padx=6)

        ttk.Button(toolbar, text="▶ Iniciar HTTP",
                   command=self._qr_ensure_http,
                   style="Green.TButton").pack(side="left", padx=6)

        ttk.Button(toolbar, text="📋 Copiar link do remoto",
                   command=self._qr_copy_remote_link,
                   style="Orange.TButton").pack(side="left", padx=6)

        ttk.Button(toolbar, text="🔢 Novo PIN",
                   command=self._qr_new_pin,
                   style="Purple.TButton").pack(side="left", padx=6)

        center = ttk.Frame(parent)
        center.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        center.columnconfigure(0, weight=1)
        center.columnconfigure(1, weight=0)
        center.rowconfigure(0, weight=1)

        info = ttk.Frame(center)
        info.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        info.columnconfigure(0, weight=1)

        ttk.Label(info, text="Pareamento BY / Controle Remoto",
                  font=("Segoe UI Semibold", 19)).grid(
            row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(
            info,
            text=(
                "Use esta tela para conectar o celular ao controle remoto do TP.\n"
                "O celular precisa estar na mesma rede do notebook, hotspot ou USB tethering.\n"
                "Aponte a câmera para o QR Code ou copie o link do remoto."
            ),
            font=("Segoe UI", 11), justify="left",
            foreground="#374151"
        ).grid(row=1, column=0, sticky="w", pady=(0, 16))

        status_box = ttk.LabelFrame(info, text="Status de pareamento", padding=10)
        status_box.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        for c in range(2):
            status_box.columnconfigure(c, weight=1)

        self._pair_http_var = tk.StringVar(value="HTTP: verificando...")
        self._pair_ws_var = tk.StringVar(value="WebSocket: verificando...")
        self._pair_remote_var = tk.StringVar(value="Celular: aguardando...")
        self._pair_network_var = tk.StringVar(value="Rede: verificando...")

        self._pair_http_lbl = ttk.Label(status_box, textvariable=self._pair_http_var,
                                        style="StatusNeutral.TLabel")
        self._pair_http_lbl.grid(row=0, column=0, sticky="ew", padx=4, pady=3)

        self._pair_ws_lbl = ttk.Label(status_box, textvariable=self._pair_ws_var,
                                      style="StatusNeutral.TLabel")
        self._pair_ws_lbl.grid(row=0, column=1, sticky="ew", padx=4, pady=3)

        self._pair_remote_lbl = ttk.Label(status_box, textvariable=self._pair_remote_var,
                                          style="StatusNeutral.TLabel")
        self._pair_remote_lbl.grid(row=1, column=0, sticky="ew", padx=4, pady=3)

        self._pair_network_lbl = ttk.Label(status_box, textvariable=self._pair_network_var,
                                           style="StatusNeutral.TLabel")
        self._pair_network_lbl.grid(row=1, column=1, sticky="ew", padx=4, pady=3)

        ttk.Label(info, text="IP detectado na rede:",
                  font=("Segoe UI Semibold", 11),
                  foreground="#6b7280").grid(row=3, column=0, sticky="w")

        self._qr_ip_var = tk.StringVar(value="Detectando...")
        ttk.Label(info, textvariable=self._qr_ip_var,
                  font=("Consolas", 17, "bold"),
                  foreground="#1d4ed8").grid(
            row=4, column=0, sticky="w", pady=(2, 12))

        ttk.Label(info, text="PIN de pareamento:",
                  font=("Segoe UI Semibold", 11),
                  foreground="#6b7280").grid(row=5, column=0, sticky="w")

        ttk.Label(info, textvariable=self.remote_pin_var,
                  font=("Consolas", 22, "bold"),
                  foreground="#7c3aed").grid(
            row=6, column=0, sticky="w", pady=(2, 12))

        ttk.Label(info, text="Link do Controle Remoto:",
                  font=("Segoe UI Semibold", 11),
                  foreground="#6b7280").grid(row=7, column=0, sticky="w")

        self._qr_url_remote_var = tk.StringVar(value="")
        lbl_remote = ttk.Label(info, textvariable=self._qr_url_remote_var,
                               font=("Consolas", 13),
                               foreground="#2563eb", cursor="hand2")
        lbl_remote.grid(row=8, column=0, sticky="w", pady=(2, 8))
        lbl_remote.bind("<Button-1>",
                        lambda _e: self._qr_open_url(self._qr_url_remote_var.get()))

        ttk.Label(info, text="Link do Teleprompter:",
                  font=("Segoe UI Semibold", 11),
                  foreground="#6b7280").grid(row=9, column=0, sticky="w")

        self._qr_url_tp_var = tk.StringVar(value="")
        lbl_tp = ttk.Label(info, textvariable=self._qr_url_tp_var,
                           font=("Consolas", 13),
                           foreground="#059669", cursor="hand2")
        lbl_tp.grid(row=10, column=0, sticky="w", pady=(2, 12))
        lbl_tp.bind("<Button-1>", lambda _e: self._qr_open_url(self._qr_url_tp_var.get()))

        self._pair_hint_var = tk.StringVar(
            value="Dica: se a rede corporativa bloquear, use hotspot do celular ou ancoragem USB."
        )
        ttk.Label(info, textvariable=self._pair_hint_var,
                  font=("Segoe UI", 10), foreground="#6b7280",
                  justify="left").grid(row=11, column=0, sticky="w", pady=(8, 0))

        self._qr_copy_status_var = tk.StringVar(value="")
        ttk.Label(info, textvariable=self._qr_copy_status_var,
                  font=("Segoe UI", 10),
                  foreground="#00875a").grid(row=12, column=0, sticky="w", pady=(8, 0))

        qr_container = ttk.LabelFrame(center, text="QR Code — Controle Remoto BY", padding=12)
        qr_container.grid(row=0, column=1, sticky="n")

        self._qr_label = ttk.Label(qr_container)
        self._qr_label.pack()

        ttk.Label(qr_container,
                  text="Aponte a câmera do celular\npara abrir o remote.html",
                  justify="center", font=("Segoe UI", 10),
                  foreground="#6b7280").pack(pady=(8, 0))

        self._qr_photo = None
        self.after(200, self._qr_refresh)

    def _write_remote_by_page(self):
        """Gera remote_by.html com PIN e controle remoto direto.

        Versão corrigida:
        - remove JavaScript quebrado que travava o touch;
        - substitui botões de segurar por Shuttle Manual;
        - mantém PLAY, PAUSE, AUTO, MANUAL, RESET, fonte e espelho.
        """
        try:
            pin = self.remote_pin_var.get().strip()
            html = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>TP IA - Controle Remoto</title>
<style>
:root{
  --bg:#080808;
  --card:#151515;
  --card2:#1f2937;
  --text:#ffffff;
  --muted:#a3a3a3;
  --blue:#2563eb;
  --green:#16a34a;
  --yellow:#d97706;
  --red:#dc2626;
  --purple:#7c3aed;
  --dark:#374151;
}
*{
  box-sizing:border-box;
  -webkit-tap-highlight-color:transparent;
}
body{
  margin:0;
  background:var(--bg);
  color:var(--text);
  font-family:Arial,sans-serif;
  padding:14px;
  overscroll-behavior:none;
}
.card{
  max-width:460px;
  margin:0 auto;
}
h1{
  font-size:23px;
  margin:10px 0;
}
.status{
  padding:10px;
  border-radius:10px;
  background:var(--card2);
  margin:10px 0;
  color:#facc15;
  font-weight:bold;
}
.pinbox{
  background:var(--card);
  border:1px solid #333;
  border-radius:16px;
  padding:18px;
  margin-top:20px;
}
input[type="text"]{
  width:100%;
  box-sizing:border-box;
  font-size:28px;
  text-align:center;
  padding:14px;
  border-radius:12px;
  border:1px solid #444;
  background:#050505;
  color:white;
  letter-spacing:.18em;
}
button{
  width:100%;
  border:0;
  border-radius:12px;
  padding:16px;
  margin:6px 0;
  font-size:18px;
  font-weight:bold;
  color:#fff;
  background:var(--blue);
  -webkit-user-select:none;
  user-select:none;
  -webkit-touch-callout:none;
  touch-action:manipulation;
}
button:active{
  transform:scale(.98);
  filter:brightness(1.2);
}
.row{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:10px;
}
.green{background:var(--green)}
.yellow{background:var(--yellow)}
.red{background:var(--red)}
.purple{background:var(--purple)}
.dark{background:var(--dark)}
.small{
  font-size:14px;
  color:var(--muted);
}
.err{
  color:#f87171;
  min-height:22px;
  margin-top:8px;
}
#control{display:none}
.shuttleBox{
  background:var(--card);
  border:1px solid #333;
  border-radius:16px;
  padding:14px;
  margin:12px 0;
}
.shuttleTitle{
  text-align:center;
  font-weight:bold;
  margin-bottom:8px;
  color:#e5e7eb;
}
#shuttle{
  width:100%;
  height:44px;
  touch-action:none;
}
.shuttleLabels{
  display:flex;
  justify-content:space-between;
  margin-top:5px;
  font-size:13px;
  color:var(--muted);
}
#shuttleValue{
  text-align:center;
  font-size:14px;
  color:#facc15;
  margin-top:8px;
  font-weight:bold;
}
#last{
  margin-top:10px;
  font-size:12px;
  color:#aaa;
  word-break:break-word;
}
</style>
</head>
<body>
<div class="card">
  <div id="pinCard" class="pinbox">
    <h1>Pareamento BY</h1>
    <div class="small">Digite o PIN exibido na aplicação.</div>
    <input id="pin" type="text" inputmode="numeric" maxlength="4" placeholder="PIN">
    <button onclick="checkPin()">Liberar controle</button>
    <div class="err" id="err"></div>
  </div>

  <div id="control">
    <h1>Controle Remoto TP IA</h1>
    <div class="status" id="status">WebSocket: conectando...</div>

    <div class="row">
      <button class="green" onclick="send({type:'control',mode:'auto'})">AUTO IA</button>
      <button class="yellow" onclick="send({type:'control',mode:'manual'})">MANUAL</button>
    </div>

    <div class="row">
      <button onclick="send({type:'control',action:'play'})">PLAY</button>
      <button class="red" onclick="send({type:'control',action:'pause'})">PAUSE</button>
    </div>

    <button class="red" onclick="send({type:'control',action:'reset'})">RESET</button>

    <div class="shuttleBox">
      <div class="shuttleTitle">SHUTTLE MANUAL</div>
      <input id="shuttle" type="range" min="-100" max="100" value="0">
      <div class="shuttleLabels">
        <span>◀ Voltar</span>
        <span>Parado</span>
        <span>Avançar ▶</span>
      </div>
      <div id="shuttleValue">Centro / parado</div>
    </div>

    <div class="row">
      <button onclick="send({type:'control',action:'font_minus'})">A− Fonte</button>
      <button onclick="send({type:'control',action:'font_plus'})">A+ Fonte</button>
    </div>

    <button class="purple" onclick="send({type:'control',action:'toggle_mirror'})">ESPELHAR / NORMAL</button>
    <div id="last">Último comando: nenhum</div>
  </div>
</div>

<script>
const REQUIRED_PIN = "__PIN__";
let ws = null;
let shuttleTimer = null;
let shuttleValue = 0;

const statusEl = document.getElementById("status");
const lastEl = document.getElementById("last");

function checkPin(){
  const typed = document.getElementById("pin").value.trim();
  if(typed !== REQUIRED_PIN){
    document.getElementById("err").textContent = "PIN incorreto.";
    return;
  }
  document.getElementById("pinCard").style.display = "none";
  document.getElementById("control").style.display = "block";
  connectWS();
}

function connectWS(){
  const host = window.location.hostname;
  const url = "ws://" + host + ":8765/sender";
  statusEl.textContent = "WebSocket: conectando em " + url;
  ws = new WebSocket(url);

  ws.onopen = () => {
    statusEl.textContent = "WebSocket: conectado";
    statusEl.style.color = "#86efac";
  };

  ws.onclose = () => {
    statusEl.textContent = "WebSocket: desconectado, reconectando...";
    statusEl.style.color = "#facc15";
    setTimeout(connectWS, 1500);
  };

  ws.onerror = () => {
    statusEl.textContent = "WebSocket: erro";
    statusEl.style.color = "#f87171";
  };
}

let eventSequence = 0;

function send(obj){
  const payload = {...obj};
  eventSequence += 1;
  payload.event_id = "remote-" + Date.now() + "-" + eventSequence;
  payload._source = "remote";
  lastEl.textContent = "Último comando: " + JSON.stringify(payload);
  if(ws && ws.readyState === WebSocket.OPEN){
    ws.send(JSON.stringify(payload));
  } else {
    statusEl.textContent = "WebSocket: não conectado";
    statusEl.style.color = "#f87171";
  }
}

function startShuttleLoop(){
  if(shuttleTimer){
    clearInterval(shuttleTimer);
  }

  shuttleTimer = setInterval(() => {
    const v = Number(shuttleValue);

    if(v === 0){
      return;
    }

    const action = v > 0 ? "forward" : "back";

    // Curva mais controlável:
    // mínimo perceptível + aumento progressivo conforme afasta do centro.
    const absV = Math.abs(v);
    const value = 0.0025 + (absV / 100) * 0.0115;

    send({
      type:"control",
      action:action,
      value:value
    });

  }, 120);
}

function stopShuttle(){
  const shuttle = document.getElementById("shuttle");
  const label = document.getElementById("shuttleValue");

  shuttleValue = 0;
  if(shuttle){
    shuttle.value = 0;
  }
  if(label){
    label.textContent = "Centro / parado";
  }

  if(shuttleTimer){
    clearInterval(shuttleTimer);
    shuttleTimer = null;
  }
}

function setupShuttle(){
  const shuttle = document.getElementById("shuttle");
  const label = document.getElementById("shuttleValue");

  if(!shuttle){
    return;
  }

  const update = (ev) => {
    if(ev){
      ev.preventDefault();
    }

    shuttleValue = Number(shuttle.value);

    if(shuttleValue === 0){
      if(label) label.textContent = "Centro / parado";
      if(shuttleTimer){
        clearInterval(shuttleTimer);
        shuttleTimer = null;
      }
      return;
    }

    const dir = shuttleValue > 0 ? "Avançando" : "Voltando";
    if(label){
      label.textContent = dir + " — intensidade " + Math.abs(shuttleValue) + "%";
    }

    if(!shuttleTimer){
      startShuttleLoop();
    }
  };

  shuttle.addEventListener("input", update, {passive:false});
  shuttle.addEventListener("change", stopShuttle);
  shuttle.addEventListener("touchend", stopShuttle, {passive:true});
  shuttle.addEventListener("mouseup", stopShuttle);
  shuttle.addEventListener("pointerup", stopShuttle);
  shuttle.addEventListener("pointercancel", stopShuttle);
}

document.getElementById("pin").addEventListener("keydown", e=>{
  if(e.key==="Enter") checkPin();
});

document.addEventListener("contextmenu", e => e.preventDefault());
document.addEventListener("selectstart", e => e.preventDefault());

window.addEventListener("blur", stopShuttle);
document.addEventListener("visibilitychange", () => {
  if(document.hidden) stopShuttle();
});

setupShuttle();
</script>
</body>
</html>""".replace("__PIN__", pin)

            (self._project_dir() / "remote_by.html").write_text(html, encoding="utf-8")
        except Exception as e:
            self.log(f"Erro ao gerar remote_by.html: {e}")

    def _qr_refresh(self):
        """Detecta IP, monta URLs, atualiza status e gera QR Code."""
        ip = self._get_local_ip()

        if self._lan_enable_error == "http":
            self._pair_http_var.set("HTTP LAN: falha")
            self._set_status_label_style(self._pair_http_lbl, "bad")
        elif self.http_running:
            self._pair_http_var.set("HTTP: online")
            self._set_status_label_style(self._pair_http_lbl, "good")
        else:
            self._pair_http_var.set("HTTP: parado")
            self._set_status_label_style(self._pair_http_lbl, "warn")

        ws_ok = bool(self.ws_proc.popen and self.ws_proc.popen.poll() is None)
        if self._lan_enable_error == "websocket":
            self._pair_ws_var.set("WebSocket LAN: falha")
            self._set_status_label_style(self._pair_ws_lbl, "bad")
        elif ws_ok:
            self._pair_ws_var.set("WebSocket: online")
            self._set_status_label_style(self._pair_ws_lbl, "good")
        else:
            self._pair_ws_var.set("WebSocket: parado")
            self._set_status_label_style(self._pair_ws_lbl, "warn")

        st = self._read_tp_status()
        if self._native_tp_is_connected():
            st["tp_connected"] = True
        remote_count = st.get("remote_count", None)
        if remote_count is None:
            self._pair_remote_var.set("Celular: N/D")
            self._set_status_label_style(self._pair_remote_lbl, "neutral")
        elif int(remote_count) > 0:
            self._pair_remote_var.set(f"Celular: conectado ({remote_count})")
            self._set_status_label_style(self._pair_remote_lbl, "good")
        else:
            self._pair_remote_var.set("Celular: aguardando conexão")
            self._set_status_label_style(self._pair_remote_lbl, "warn")

        if not ip:
            self._qr_ip_var.set("IPv4 LAN não disponível")
            self._qr_url_remote_var.set("")
            self._qr_url_tp_var.set("")
            self._pair_network_var.set("Rede: nenhuma interface LAN válida")
            self._set_status_label_style(self._pair_network_lbl, "warn")
            self._pair_hint_var.set(
                "Conecte o notebook a uma rede Wi-Fi ou Ethernet válida."
            )
            self._qr_label.configure(
                text="QR indisponível sem uma interface LAN ativa.", image=""
            )
            return False

        self._qr_ip_var.set(ip)

        if not self.lan_access_enabled:
            self._qr_url_remote_var.set("")
            self._qr_url_tp_var.set("")
            if self._lan_enable_error in ("http", "websocket"):
                self._pair_network_var.set("Rede: promoção LAN falhou")
                self._pair_hint_var.set(
                    "Corrija a falha indicada no serviço de rede e tente novamente."
                )
            else:
                self._pair_network_var.set("Rede: acesso LAN ainda não habilitado")
                self._pair_hint_var.set(
                    'Use "Atualizar IP / QR Code" para habilitar o acesso LAN.'
                )
            self._set_status_label_style(self._pair_network_lbl, "warn")
            self._qr_label.configure(
                text='Acesso LAN ainda não habilitado.\nUse "Atualizar IP / QR Code".',
                image="",
            )
            return False

        self._write_remote_by_page()
        pin = self.remote_pin_var.get().strip()
        url_remote = build_remote_url(ip, HTTP_PORT, pin)
        url_tp = f"http://{ip}:{HTTP_PORT}/tp.html"
        self._qr_url_remote_var.set(url_remote)
        self._qr_url_tp_var.set(url_tp)

        self._pair_network_var.set(f"Rede: {ip}")
        self._set_status_label_style(self._pair_network_lbl, "good")
        self._pair_hint_var.set(
            "Se não abrir no celular, a rede pode bloquear comunicação entre dispositivos. Use hotspot ou USB tethering."
        )

        if QR_OK:
            self._qr_generate(url_remote)
        else:
            self._qr_label.configure(
                text=(
                    "qrcode/Pillow não instalados.\n\n"
                    "Execute:\n"
                    "pip install qrcode pillow"
                ),
                image=""
            )
        return True

    def _qr_enable_and_refresh(self):
        """Ação explícita do operador para disponibilizar o pareamento na LAN."""
        self._enable_lan_for_qr()
        return self._qr_refresh()

    def _qr_copy_remote_link(self):
        """Copia o link do controle remoto para a área de transferência."""
        url = self._qr_url_remote_var.get().strip()
        if not url:
            self._qr_refresh()
            url = self._qr_url_remote_var.get().strip()

        try:
            self.clipboard_clear()
            self.clipboard_append(url)
            self._qr_copy_status_var.set("Link do controle remoto copiado.")
            self.log(f"Link remoto copiado: {mask_remote_url(url)}")
        except Exception as e:
            self._qr_copy_status_var.set(f"Erro ao copiar link: {e}")
            self.log(f"Erro ao copiar link remoto: {e}")

    def _qr_new_pin(self):
        """Gera um novo PIN visual para pareamento operacional."""
        self.remote_pin_var.set(str(random.randint(1000, 9999)))
        self._write_remote_by_page()
        self._qr_refresh()
        self._qr_copy_status_var.set("Novo PIN de pareamento gerado e QR atualizado.")
        self.log("Novo PIN de pareamento gerado.")

    def _qr_generate(self, url: str):
        """Gera a imagem do QR Code e exibe no label."""
        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=7,
                border=3,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            # Redimensiona para caber bem na janela
            img = img.resize((260, 260), Image.NEAREST)
            self._qr_photo = ImageTk.PhotoImage(img)
            self._qr_label.configure(image=self._qr_photo, text="")
        except Exception as e:
            self._qr_label.configure(text=f"Erro ao gerar QR:\n{e}", image="")

    def _enable_lan_for_qr(self) -> bool:
        """Promove somente HTTP/WebSocket para LAN após ação explícita do usuário."""
        self._lan_enable_error = None
        ip = self._get_local_ip()
        if not ip:
            self.log("QR Celular: nenhuma interface IPv4 LAN física e ativa foi encontrada.")
            messagebox.showwarning(
                "Rede LAN não encontrada",
                "Não foi encontrada uma interface Wi-Fi ou Ethernet ativa com IPv4 válido.",
            )
            return False

        http_was_running = self.http_running
        ws_running = bool(self.ws_proc.popen and self.ws_proc.popen.poll() is None)
        restart_http = self.http_running and self._http_bind_host != "0.0.0.0"
        restart_ws = ws_running and self._ws_lan_enabled is not True

        if restart_http:
            self.stop_http()
        if restart_ws:
            self.stop_ws()

        self.lan_access_enabled = True

        if not self.http_running and not self.start_http():
            self.lan_access_enabled = False
            if ws_running and not (self.ws_proc.popen and self.ws_proc.popen.poll() is None):
                self.start_ws()
            self._lan_enable_error = "http"
            self.log("QR Celular: falha técnica ao disponibilizar HTTP na LAN.")
            return False
        if not (self.ws_proc.popen and self.ws_proc.popen.poll() is None):
            if not self.start_ws():
                if self.http_running:
                    self.stop_http()
                self.lan_access_enabled = False
                if http_was_running:
                    self.start_http()
                if ws_running:
                    self.start_ws()
                self._lan_enable_error = "websocket"
                self.log("QR Celular: falha técnica ao disponibilizar WebSocket na LAN.")
                return False

        self.log(f"QR Celular: acesso LAN habilitado em {ip}.")
        return True

    def _qr_ensure_http(self):
        """Habilita acesso LAN explicitamente e atualiza o QR."""
        if self._enable_lan_for_qr():
            self.after(300, self._qr_refresh)
        else:
            self._qr_refresh()

    def _qr_open_url(self, url: str):
        """Abre a URL no navegador local ao clicar no link."""
        if url:
            webbrowser.open(url)

    # ══════════════════════════════════════════════════════════════════
    # Helpers gerais
    # ══════════════════════════════════════════════════════════════════

    def _python_config_path(self) -> Path:
        return self.project_dir / CONFIG_FILE

    @staticmethod
    def _python_runs(path: str) -> bool:
        if not path or not os.path.isfile(path):
            return False
        try:
            result = subprocess.run(
                [path, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _resolve_python_cmd(self) -> tuple[str, str]:
        config_path = self._python_config_path()
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            saved = os.path.abspath(str(data.get("python_exe", "")).strip())
            if self._python_runs(saved):
                return saved, "saved"
        except Exception:
            pass

        project_venv = os.path.abspath(str(self.project_dir / ".venv" / "Scripts" / "python.exe"))
        if self._python_runs(project_venv):
            return project_venv, "project_venv"
        return os.path.abspath(sys.executable), "fallback"

    def _log_python_config(self, path: str, source: str):
        self.log(f"PYTHON_CONFIG path={path}")
        self.log(f"PYTHON_CONFIG exists={os.path.isfile(path)}")
        self.log(f"PYTHON_CONFIG source={source}")

    def _save_python_config(self, path: str) -> bool:
        path = os.path.abspath(str(path).strip())
        if not os.path.isabs(path) or not os.path.isfile(path):
            return False
        try:
            self._python_config_path().write_text(
                json.dumps({"python_exe": path}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.python_cmd.set(path)
            self._python_config_source = "saved"
            return True
        except Exception as exc:
            self.log(f"PYTHON_CONFIG save_error={exc}")
            return False

    def _project_dir(self) -> Path:
        return Path(self.project_var.get()).expanduser().resolve()

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}\n")

    def _drain_log_queue(self):
        try:
            count = 0
            while count < 25:
                msg = self.log_queue.get_nowait()
                self.log_text.insert("end", msg)
                count += 1

            # Mantém o log visual leve para não pesar CPU/memória.
            try:
                total_lines = int(self.log_text.index("end-1c").split(".")[0])
                if total_lines > 600:
                    self.log_text.delete("1.0", f"{total_lines - 500}.0")
            except Exception:
                pass

            if count:
                self.log_text.see("end")

        except queue.Empty:
            pass

        self.after(500, self._drain_log_queue)

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    def select_project_dir(self):
        chosen = filedialog.askdirectory(initialdir=str(self._project_dir()))
        if chosen:
            self.project_var.set(chosen)
            self._load_project_files()

    def _load_project_files(self):
        p = self._project_dir()
        self.project_dir = p
        self._ensure_roteiros_folder()
        self.load_roteiro()
        self._load_device_from_main()
        self._check_required_files()
        self.refresh_roteiros_list()
        try:
            self._write_remote_by_page()
        except Exception:
            pass

    def _check_required_files(self):
        p = self._project_dir()
        missing = [n for n in ["scroll_server.py", "main_align_ws.py", "tp.html", "roteiro.txt"]
                   if not (p / n).exists()]
        if missing:
            self.log("⚠️ Arquivos faltando: " + ", ".join(missing))
        else:
            self.log("✅ Arquivos principais encontrados.")

    def _load_device_from_main(self):
        f = self._project_dir() / "main_align_ws.py"
        if not f.exists():
            return
        txt = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^DEVICE\s*=\s*(\d+)", txt, flags=re.MULTILINE)
        if m:
            self.device_var.set(m.group(1))

    def _roteiros_dir(self) -> Path:
        return self._project_dir() / "roteiros"

    def _ensure_roteiros_folder(self):
        try:
            pasta = self._roteiros_dir()
            pasta.mkdir(exist_ok=True)
            ativo = self._project_dir() / "roteiro.txt"
            inicial = pasta / "roteiro_atual.txt"
            if ativo.exists() and not inicial.exists():
                inicial.write_text(ativo.read_text(encoding="utf-8", errors="ignore"),
                                   encoding="utf-8")
        except Exception as e:
            self.log(f"Erro ao preparar pasta roteiros: {e}")

    def refresh_and_reload_roteiros(self):
        """Atualiza a lista da biblioteca e recarrega o TP nativo."""
        self.refresh_roteiros_list()
        self.reload_active_and_tp()

    def refresh_roteiros_list(self):
        self._ensure_roteiros_folder()
        try:
            arquivos = sorted([p.name for p in self._roteiros_dir().glob("*.txt")])
            self.roteiro_combo["values"] = arquivos
            if arquivos and self.roteiro_selected_var.get() not in arquivos:
                self.roteiro_selected_var.set(arquivos[0])
            self.log(f"Biblioteca de roteiros: {len(arquivos)} arquivo(s).")
        except Exception as e:
            self.log(f"Erro ao listar roteiros: {e}")

    def _safe_roteiro_name(self, name: str) -> str:
        name = name.strip()
        name = re.sub(r"[^\w\- .áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]", "_", name)
        if not name.lower().endswith(".txt"):
            name += ".txt"
        return name

    def preview_selected_roteiro(self):
        """Mostra o roteiro selecionado na caixa, sem aplicar no TP."""
        name = self.roteiro_selected_var.get().strip()
        if not name:
            return
        src = self._roteiros_dir() / name
        if not src.exists():
            return
        try:
            texto = src.read_text(encoding="utf-8")
            self.roteiro_text.delete("1.0", "end")
            self.roteiro_text.insert("1.0", texto)
            self.log(f"Roteiro selecionado: {name}")
        except Exception as e:
            self.log(f"Erro ao selecionar roteiro: {e}")

    def open_selected_roteiro(self):
        name = self.roteiro_selected_var.get().strip()
        if not name:
            messagebox.showwarning("Roteiro", "Selecione um roteiro na lista.")
            return
        src = self._roteiros_dir() / name
        if not src.exists():
            messagebox.showerror("Roteiro", f"Arquivo não encontrado:\n{src}")
            return
        try:
            text = src.read_text(encoding="utf-8")
            self.roteiro_text.delete("1.0", "end")
            self.roteiro_text.insert("1.0", text)
            (self._project_dir() / "roteiro.txt").write_text(
                text.strip() + "\n", encoding="utf-8")
            self.log(f"Roteiro carregado e ativado: {name}")
            self.apply_current_script_to_tp()
            try:
                if self.tp_native_window is not None and self.tp_native_window.winfo_exists():
                    self.tp_native_window.reload_script()
                    self.log("TP nativo atualizado após abrir roteiro.")
                    try:
                        self._restart_ai_after_reset()
                    except Exception:
                        pass
            except Exception as e:
                self.log(f"Erro ao atualizar TP após abrir roteiro: {e}")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir o roteiro:\n{e}")

    def new_roteiro(self):
        name = simpledialog.askstring("Novo roteiro", "Nome do novo roteiro:")
        if not name:
            return
        name = self._safe_roteiro_name(name)
        path = self._roteiros_dir() / name
        if path.exists():
            messagebox.showwarning("Roteiro", "Já existe um roteiro com esse nome.")
            return
        try:
            path.write_text("Digite o novo roteiro aqui.\n", encoding="utf-8")
            self.refresh_roteiros_list()
            self.roteiro_selected_var.set(name)
            self.open_selected_roteiro()
            self.log(f"Novo roteiro criado: {name}")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível criar o roteiro:\n{e}")

    def save_roteiro_as(self):
        name = simpledialog.askstring("Salvar como", "Nome do roteiro:")
        if not name:
            return
        name = self._safe_roteiro_name(name)
        path = self._roteiros_dir() / name
        if path.exists() and not messagebox.askyesno(
                "Sobrescrever", f"{name} já existe. Deseja sobrescrever?"):
            return
        try:
            texto = self.roteiro_text.get("1.0", "end").strip() + "\n"
            path.write_text(texto, encoding="utf-8")
            (self._project_dir() / "roteiro.txt").write_text(texto, encoding="utf-8")
            self.refresh_roteiros_list()
            self.roteiro_selected_var.set(name)
            self.log(f"Roteiro salvo como: {name}")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível salvar como:\n{e}")

    def delete_selected_roteiro(self):
        name = self.roteiro_selected_var.get().strip()
        if not name:
            return
        path = self._roteiros_dir() / name
        if not path.exists():
            return
        if not messagebox.askyesno("Excluir roteiro", f"Deseja excluir {name}?"):
            return
        try:
            path.unlink()
            self.roteiro_selected_var.set("")
            self.refresh_roteiros_list()
            self.log(f"Roteiro excluído: {name}")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível excluir:\n{e}")

    def open_roteiros_folder(self):
        pasta = self._roteiros_dir()
        pasta.mkdir(exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(pasta))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(pasta)])
            else:
                subprocess.Popen(["xdg-open", str(pasta)])
        except Exception as e:
            self.log(f"Erro ao abrir pasta de roteiros: {e}")

    def load_roteiro(self):
        roteiro = self._project_dir() / "roteiro.txt"
        if roteiro.exists():
            try:
                self.roteiro_text.delete("1.0", "end")
                self.roteiro_text.insert("1.0", roteiro.read_text(encoding="utf-8"))
                self.log("📄 roteiro.txt carregado.")
            except Exception as e:
                self.log(f"Erro ao carregar roteiro: {e}")
        else:
            self.roteiro_text.delete("1.0", "end")
            self.log("⚠️ roteiro.txt não encontrado.")

    def reload_active_and_tp(self):
        """Atualiza o roteiro ativo e recarrega o TP nativo.

        Use esta função nos botões:
        - Atualizar
        - Recarregar ativo
        - Abrir roteiro

        Ela garante que o texto carregado na aplicação também seja aplicado
        na janela do Teleprompter nativo.
        """
        try:
            # Primeiro salva o conteúdo atual da caixa no roteiro.txt.
            texto = self.roteiro_text.get("1.0", "end").strip() + "\n"
            (self._project_dir() / "roteiro.txt").write_text(texto, encoding="utf-8")

            selected = self.roteiro_selected_var.get().strip()
            if selected:
                lib_file = self._roteiros_dir() / selected
                if lib_file.exists():
                    lib_file.write_text(texto, encoding="utf-8")

            self.log("Roteiro ativo salvo para atualização do TP.")
        except Exception as e:
            self.log(f"Erro ao salvar roteiro antes do reload: {e}")

        try:
            self._send_tp_command({
                "type": "control",
                "action": "reload",
                "script_version": time.time_ns(),
            })
            self.log("Fluxo único de reload enviado à IA e aos TPs.")
        except Exception as e:
            self.log(f"Erro ao enviar comando reload: {e}")

    def apply_current_script_to_tp(self):
        """Salva e aplica o roteiro por um único fluxo coordenado."""
        self.reload_active_and_tp()

    def save_roteiro(self):
        roteiro = self._project_dir() / "roteiro.txt"
        try:
            texto = self.roteiro_text.get("1.0", "end").strip() + "\n"
            roteiro.write_text(texto, encoding="utf-8")
            selected = self.roteiro_selected_var.get().strip()
            if selected:
                lib_file = self._roteiros_dir() / selected
                if lib_file.exists():
                    lib_file.write_text(texto, encoding="utf-8")
                    self.log(f"roteiro.txt e biblioteca salvos: {selected}")
                    return
            self.log("roteiro.txt salvo.")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível salvar roteiro.txt:\n{e}")

    def save_device_to_main(self):
        value = self.device_var.get().strip()
        if not value.isdigit():
            messagebox.showwarning("Device inválido", "Informe apenas o número do device de áudio.")
            return
        main_file = self._project_dir() / "main_align_ws.py"
        if not main_file.exists():
            messagebox.showerror("Erro", "main_align_ws.py não encontrado.")
            return
        txt = main_file.read_text(encoding="utf-8", errors="ignore")
        new_txt, n = re.subn(r"^DEVICE\s*=\s*\d+", f"DEVICE = {value}", txt,
                             count=1, flags=re.MULTILINE)
        if n == 0:
            messagebox.showwarning("Não encontrado",
                                   "Não encontrei a linha DEVICE = ... no main_align_ws.py")
            return
        main_file.write_text(new_txt, encoding="utf-8")
        self.log(f"🎙️ DEVICE atualizado para {value} no main_align_ws.py")

    def test_python(self):
        cmd = os.path.abspath(self.python_cmd.get().strip())
        try:
            if not os.path.isfile(cmd):
                raise FileNotFoundError(cmd)
            result = subprocess.run([cmd, "--version"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=5)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout).strip())
            out = (result.stdout or result.stderr).strip()
            self._save_python_config(cmd)
            self._log_python_config(cmd, "saved")
            self.log(f"Python: {out}")
        except Exception as e:
            messagebox.showerror("Erro", f"Não consegui executar Python:\n{e}")

    def _start_process(self, mp: ManagedProcess):
        if mp.popen and mp.popen.poll() is None:
            self.log(f"{mp.name} já está rodando.")
            return True
        script_path = self._project_dir() / mp.script
        if not script_path.exists():
            messagebox.showerror("Arquivo não encontrado",
                                 f"Não encontrei {mp.script} na pasta do projeto.")
            return False
        python_exe = os.path.abspath(self.python_cmd.get().strip())
        source = getattr(self, "_python_config_source", "saved")
        self._log_python_config(python_exe, source)
        if not os.path.isfile(python_exe) or not self._python_runs(python_exe):
            messagebox.showerror("Python inválido", f"Interpretador não executável:\n{python_exe}")
            return False
        if mp is self.ai_proc and not (self._project_dir() / "main_align_ws.py").is_file():
            messagebox.showerror("Arquivo não encontrado", "main_align_ws.py não encontrado.")
            return False
        self._save_python_config(python_exe)
        cmd = [python_exe, str(script_path)]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"]       = "1"
        if mp is self.ws_proc:
            env["TELEPROMPTER_ALLOW_LAN"] = "1" if self.lan_access_enabled else "0"
        try:
            mp.popen = subprocess.Popen(
                cmd,
                cwd=str(self._project_dir()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            if mp is self.ws_proc:
                self._ws_lan_enabled = self.lan_access_enabled
            self.log(f"▶ {mp.name} iniciado.")
            threading.Thread(target=self._read_process_output, args=(mp,), daemon=True).start()
            return True
        except Exception as e:
            messagebox.showerror("Erro", f"Não consegui iniciar {mp.name}:\n{e}")
            return False

    def _read_process_output(self, mp: ManagedProcess):
        if not mp.popen or not mp.popen.stdout:
            return
        for line in mp.popen.stdout:
            clean = line.rstrip()
            self.log(f"[{mp.name}] {clean}")

        self.log(f"■ {mp.name} finalizado.")

    def _stop_process(self, mp: ManagedProcess):
        if not mp.popen or mp.popen.poll() is not None:
            self.log(f"{mp.name} já está parado.")
            return
        self.log(f"Parando {mp.name}...")
        try:
            if os.name == "nt":
                mp.popen.terminate()
            else:
                mp.popen.send_signal(signal.SIGTERM)
            try:
                mp.popen.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mp.popen.kill()
        except Exception as e:
            self.log(f"Erro ao parar {mp.name}: {e}")

    # ── HTTP ──────────────────────────────────────────────────────────

    def start_http(self):
        if self.http_running:
            self.log("HTTP já está rodando.")
            return True
        try:
            bind_host = "0.0.0.0" if self.lan_access_enabled else HTTP_HOST
            handler = lambda *args, **kwargs: QuietHTTPRequestHandler(
                *args, directory=str(self._project_dir()), **kwargs)
            self.http_server = ThreadingHTTPServer((bind_host, HTTP_PORT), handler)
            self.http_thread = threading.Thread(
                target=self.http_server.serve_forever, daemon=True)
            self.http_thread.start()
            self.http_running = True
            self._http_bind_host = bind_host
            self.log(f"🌐 HTTP bind {bind_host}:{HTTP_PORT}")
            return True
        except OSError as e:
            self.log(f"⚠️ HTTP na porta {HTTP_PORT}: {e}")
            messagebox.showerror("Erro HTTP", f"Não consegui iniciar HTTP na porta {HTTP_PORT}.")
            return False

    def stop_http(self):
        if self.http_server:
            self.http_server.shutdown()
            self.http_server.server_close()
        self.http_server = None
        self.http_thread = None
        self.http_running = False
        self._http_bind_host = None
        self.log("■ HTTP parado.")

    # ── Ações ─────────────────────────────────────────────────────────

    def start_ws(self):   return self._start_process(self.ws_proc)
    def stop_ws(self):
        self._stop_process(self.ws_proc)
        self._ws_lan_enabled = None
    def start_ai(self):   self._start_process(self.ai_proc)
    def stop_ai(self):    self._stop_process(self.ai_proc)

    def start_all(self):
        self.start_ws()
        self.start_http()
        self.after(800, self.start_ai)
        self.after(1500, self._reload_tp_if_connected)
        self.after(1700, self._reload_native_if_open)

    def _reload_native_if_open(self):
        """Recarrega o TP nativo se ele estiver aberto."""
        try:
            if self.tp_native_window is not None and self.tp_native_window.winfo_exists():
                self.tp_native_window.reload_script()
        except Exception:
            pass

    def _reload_tp_if_connected(self):
        if self._tp_is_connected():
            self._send_tp_command({"type": "control", "action": "reload"})
            self.log("TP já estava aberto. Reload automático enviado.")

    def stop_all(self):
        self.stop_ai()
        self.stop_ws()
        self.stop_http()
        self.log("Serviços do TP finalizados.")

    def _tp_is_connected(self) -> bool:
        try:
            status_file = self._project_dir() / "tp_status.json"
            if not status_file.exists():
                return False
            data = json.loads(status_file.read_text(encoding="utf-8"))
            if time.time() - float(data.get("updated", 0)) > 10:
                return False
            return int(data.get("tp_count", 0)) > 0
        except Exception:
            return False

    def _open_tp_url(self, mirror: bool = False):
        if not self.http_running:
            self.start_http()
        if self._tp_is_connected():
            if mirror:
                self._send_tp_command({"type": "control", "action": "toggle_mirror"})
                self.log("TP conectado. Modo espelhado alternado.")
            else:
                self._send_tp_command({"type": "control", "action": "reload"})
                self.log("TP conectado. Reload enviado.")
            return
        url = f"http://{HTTP_OPEN_HOST}:{HTTP_PORT}/tp.html"
        if mirror:
            url += "?mirror=1"
        firefox_paths = [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ]
        firefox = next((fp for fp in firefox_paths if Path(fp).exists()), None)
        try:
            if firefox:
                subprocess.Popen([firefox, url])
            else:
                webbrowser.open(url)
            self.log(f"Abrindo TP {'espelhado' if mirror else 'normal'}: {url}")
        except Exception as e:
            self.log(f"Erro ao abrir navegador: {e}")

    def toggle_native_mirror(self):
        """Alterna espelhamento no TP nativo sem abrir página web."""
        if self.tp_native_window is None or not self.tp_native_window.winfo_exists():
            self.open_tp_native_window()

        self._deliver_to_native_tp({"type": "control", "action": "toggle_mirror"})
        self.log("Espelhamento alternado no TP nativo.")

    def open_tp(self):        self._open_tp_url(mirror=False)
    def open_tp_mirror(self): self.toggle_native_mirror()

    def open_remote(self):
        """Abre a aba de controle remoto integrado."""
        self.notebook.select(self._tab_remote)

    def open_remote_qr(self):
        """Abre a aba de acesso pelo celular (IP + QR Code)."""
        if self.notebook.select() == str(self._tab_qr):
            self._qr_enable_and_refresh()
        else:
            self.notebook.select(self._tab_qr)

    def _on_notebook_tab_changed(self, _event=None):
        """Disponibiliza o remoto uma vez a cada entrada na aba de pareamento."""
        if self.notebook.select() == str(self._tab_qr):
            self._qr_enable_and_refresh()

    def _deliver_to_native_tp(self, payload: dict):
        """Entrega comando diretamente ao TP nativo, sem depender do navegador.

        Isso resolve:
        - manual da aplicação não atuar no TP nativo;
        - remoto conectar, mas não atuar;
        - IA enviando scroll no WebSocket sem mover o Canvas.
        """
        try:
            if self.tp_native_window is not None and self.tp_native_window.winfo_exists():
                key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                now = time.time()
                if getattr(self.tp_native_window, "_last_payload_key", None) == key and now - getattr(self.tp_native_window, "_last_payload_ts", 0) < 0.12:
                    return
                self.tp_native_window._last_payload_key = key
                self.tp_native_window._last_payload_ts = now
                self.tp_native_window.handle_message(payload)
        except Exception as e:
            self.log(f"Erro ao entregar comando ao TP nativo: {e}")

    def _deliver_to_native_tp_threadsafe(self, payload: dict):
        """Versão segura para ser chamada por threads de leitura de log/processo."""
        try:
            self.after(0, lambda p=payload: self._deliver_to_native_tp(p))
        except Exception:
            pass

    def _bridge_ws_line_to_native(self, line: str):
        """Captura mensagens JSON do scroll_server e aplica no TP nativo.

        Evita eco de comandos enviados pela própria GUI para não duplicar
        toggle de PLAY/PAUSE, espelho, etc.
        """
        if "{" not in line or "}" not in line:
            return

        try:
            start = line.find("{")
            end = line.rfind("}") + 1
            raw = line[start:end]
            payload = json.loads(raw)

            if not isinstance(payload, dict):
                return

            # Ignora eco dos comandos que a própria GUI já aplicou localmente.
            if payload.get("_source") == "gui":
                return

            if payload.get("type") in ("scroll", "control"):
                if payload.get("type") == "control" and payload.get("action") == "reset":
                    self.after(0, lambda: self._update_operator_mode_display("auto"))
                self._deliver_to_native_tp_threadsafe(payload)

        except Exception:
            pass


    def _send_tp_command(self, payload: dict):
        """Envia comando ao TP sem travar a interface.

        Aplica direto no TP nativo e envia ao WebSocket em background.
        O envio WS leva _source='gui' para evitar eco duplicado no Canvas.
        """
        payload = dict(payload)
        if payload.get("type") == "control" and payload.get("action") == "reset":
            payload.setdefault("session_id", f"reset-{time.time_ns()}")
            self._update_operator_mode_display("auto")
        if payload.get("type") == "control":
            payload.setdefault("event_id", f"gui-{time.time_ns()}")
            trace = payload.setdefault("_trace", {})
            trace.setdefault("t_click", time.perf_counter())
            trace["t_gui_send"] = time.perf_counter()

        try:
            self.log(f"Comando TP: {payload}")
        except Exception:
            pass

        try:
            self._deliver_to_native_tp(payload)
        except Exception as e:
            try:
                self.log(f"Erro ao aplicar comando no TP nativo: {e}")
            except Exception:
                pass

        ws_payload = dict(payload)
        ws_payload["_source"] = "gui"
        ws_payload["_trace"] = dict(payload.get("_trace", {}))
        ws_payload["_trace"]["t_gui_queue"] = time.perf_counter()
        self._control_ws_queue.put(ws_payload)

    def _send_tp_command_ws_background(self, payload: dict):
        """Compatibilidade interna: enfileira no sender persistente."""
        self._control_ws_queue.put(dict(payload))

    def _control_ws_thread_main(self):
        try:
            asyncio.run(self._control_ws_loop())
        except Exception as e:
            self.log(f"Sender persistente encerrado: {e}")

    async def _control_ws_loop(self):
        uri = f"ws://127.0.0.1:{WS_PORT}/sender"
        pending = None
        while not self._control_ws_stop.is_set():
            try:
                async with websockets.connect(
                    uri, ping_interval=10, ping_timeout=10, close_timeout=1
                ) as ws:
                    while not self._control_ws_stop.is_set():
                        if pending is None:
                            pending = await asyncio.to_thread(self._control_ws_queue.get)
                        if pending is None:
                            return
                        trace = pending.setdefault("_trace", {})
                        trace["t_ws_send"] = time.perf_counter()
                        await ws.send(json.dumps(pending, ensure_ascii=False))
                        base = trace.get("t_click", trace["t_ws_send"])
                        self.log(
                            "TRACE sender persistente: "
                            f"click→ws_send={(trace['t_ws_send'] - base) * 1000:.2f} ms"
                        )
                        pending = None
            except Exception:
                await asyncio.sleep(0.05)

    def _update_operator_mode_display(self, mode: str):
        self.mode_status_var.set(f"MODO: {mode.upper()}")
        self.btn_mode_auto.configure(style="Green.TButton" if mode == "auto" else "TButton")
        self.btn_mode_manual.configure(style="Orange.TButton" if mode == "manual" else "TButton")

    def _set_operator_mode(self, mode: str):
        clicked_at = time.perf_counter()
        self._update_operator_mode_display(mode)
        self.log(f"TRACE {mode.upper()} clique: t={clicked_at:.6f}")
        self._send_tp_command({
            "type": "control",
            "mode": mode,
            "_trace": {"t_click": clicked_at},
        })


    def _restart_ai_after_reset(self):
        """Compatibilidade: o reset agora é enviado ao processo da IA por WebSocket."""
        self._send_tp_command({"type": "control", "action": "reset"})

    def tp_auto(self):       self._set_operator_mode("auto")
    def tp_manual(self):     self._set_operator_mode("manual")
    def tp_play_pause(self): self._send_tp_command({"type": "control", "action": "toggle_play"})
    def tp_reset(self):
        self._send_tp_command({"type": "control", "action": "reset"})

    def tp_speed_slow(self):
        self.manual_speed_var.set(0.0006)
        self.tp_set_speed()

    def tp_speed_normal(self):
        self.manual_speed_var.set(0.0012)
        self.tp_set_speed()

    def tp_speed_fast(self):
        self.manual_speed_var.set(0.0020)
        self.tp_set_speed()

    def tp_set_speed(self):
        speed = max(0.0003, min(0.0030, float(self.manual_speed_var.get())))
        self.manual_speed_var.set(speed)
        self.manual_speed_label_var.set(f"Velocidade: {speed:.4f}")
        self._send_tp_command({"type": "control", "speed": speed})

    def tp_hold_start(self, action: str):
        self.hold_action = action
        self._tp_hold_tick()

    def tp_hold_stop(self):
        self.hold_action = None
        if self.hold_job is not None:
            try:
                self.after_cancel(self.hold_job)
            except Exception:
                pass
            self.hold_job = None

    def _tp_hold_tick(self):
        if not self.hold_action:
            return
        self._send_tp_command(
            {"type": "control", "action": self.hold_action, "value": 0.006})
        self.hold_job = self.after(120, self._tp_hold_tick)

    def open_folder(self):
        p = self._project_dir()
        try:
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            self.log(f"Erro ao abrir pasta: {e}")

    def list_mics(self):
        code = (
            "import sounddevice as sd\n"
            "for i,d in enumerate(sd.query_devices()):\n"
            "    if d.get('max_input_channels',0)>0:\n"
            "        print(f'{i} | ch={d[\"max_input_channels\"]} |"
            " sr={int(d[\"default_samplerate\"])} | {d[\"name\"]}')\n"
        )
        try:
            result = subprocess.run(
                [self.python_cmd.get().strip(), "-c", code],
                cwd=str(self._project_dir()),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10,
            )
            self.log("🎙️ Microfones:\n" + (result.stdout or result.stderr or "Nada retornado."))
        except Exception as e:
            self.log(f"Erro ao listar mics: {e}")

    # ── Status ────────────────────────────────────────────────────────

    def _set_status_label_style(self, label, state: str):
        mapping = {"good": "StatusGood.TLabel", "warn": "StatusWarn.TLabel",
                   "bad":  "StatusBad.TLabel",  "neutral": "StatusNeutral.TLabel"}
        try:
            label.configure(style=mapping.get(state, "StatusNeutral.TLabel"))
        except Exception:
            pass

    def _native_tp_is_connected(self) -> bool:
        """Considera TP nativo conectado quando a janela Canvas está aberta."""
        try:
            return bool(self.tp_native_window is not None and self.tp_native_window.winfo_exists())
        except Exception:
            return False

    def _read_tp_status(self):
        try:
            status_file = self._project_dir() / "tp_status.json"
            if not status_file.exists():
                return {"tp_count": 0, "remote_count": None, "updated": 0}
            data = json.loads(status_file.read_text(encoding="utf-8"))
            updated = float(data.get("updated", 0))
            if time.time() - updated > 10:
                return {"tp_count": 0, "remote_count": None, "updated": updated}
            return {"tp_count": int(data.get("tp_count", 0)),
                    "remote_count": data.get("remote_count", None),
                    "updated": updated}
        except Exception:
            return {"tp_count": 0, "remote_count": None, "updated": 0}

    def _update_broadcast_status(self):
        ws_ok   = bool(self.ws_proc.popen and self.ws_proc.popen.poll() is None)
        http_ok = self.http_running
        ai_ok   = bool(self.ai_proc.popen and self.ai_proc.popen.poll() is None)

        self.status_ws.set("WebSocket: online"  if ws_ok   else "WebSocket: parado")
        self.status_http.set("HTTP: online"     if http_ok else "HTTP: parado")
        self.status_ai.set("IA/Voz: online"     if ai_ok   else "IA/Voz: parada")

        self._set_status_label_style(self.status_ws_label,   "good" if ws_ok   else "bad")
        self._set_status_label_style(self.status_http_label, "good" if http_ok else "bad")
        self._set_status_label_style(self.status_ai_label,   "good" if ai_ok   else "bad")

        st = self._read_tp_status()
        tp_count     = st.get("tp_count", 0)
        remote_count = st.get("remote_count", None)

        native_tp_ok = self._native_tp_is_connected()

        if native_tp_ok:
            self.status_tp_conn.set("TP nativo: conectado")
            self._set_status_label_style(self.status_tp_label, "good")
        elif tp_count > 0:
            self.status_tp_conn.set(f"TP browser: conectado ({tp_count})")
            self._set_status_label_style(self.status_tp_label, "good")
        else:
            self.status_tp_conn.set("TP: desconectado")
            self._set_status_label_style(self.status_tp_label, "bad" if ws_ok else "neutral")

        if remote_count is None:
            self.status_remote_conn.set("Remoto: N/D")
            self._set_status_label_style(self.status_remote_label, "neutral")
        elif int(remote_count) > 0:
            self.status_remote_conn.set(f"Remoto: conectado ({remote_count})")
            self._set_status_label_style(self.status_remote_label, "good")
        else:
            self.status_remote_conn.set("Remoto: desconectado")
            self._set_status_label_style(self.status_remote_label, "warn")

        if ws_ok and http_ok and ai_ok and (tp_count > 0 or self._native_tp_is_connected()):
            self.status_summary.set("Sistema pronto")
            self._set_status_label_style(self.status_summary_label, "good")
        elif ws_ok or http_ok or ai_ok:
            self.status_summary.set("Sistema parcial")
            self._set_status_label_style(self.status_summary_label, "warn")
        else:
            self.status_summary.set("Sistema parado")
            self._set_status_label_style(self.status_summary_label, "bad")

        # Atualiza status no painel remoto integrado
        if ws_ok:
            self._remote_status_var.set("✅ WebSocket online — comandos ativos")
        else:
            self._remote_status_var.set("⚠️ WebSocket offline — inicie os serviços primeiro")

    _qr_refresh_counter = 0

    def _refresh_status_loop(self):
        self._update_broadcast_status()
        # Atualiza status HTTP no painel QR a cada 5 s
        self._qr_refresh_counter += 1
        if self._qr_refresh_counter >= 5:
            self._qr_refresh_counter = 0
            try:
                self._qr_refresh()
            except Exception:
                pass
        self.after(1000, self._refresh_status_loop)

    def on_close(self):
        if messagebox.askokcancel("Sair", "Deseja encerrar a interface e parar os processos?"):
            python_exe = os.path.abspath(self.python_cmd.get().strip())
            if self._python_runs(python_exe):
                self._save_python_config(python_exe)
            self._control_ws_stop.set()
            self._control_ws_queue.put(None)
            self.close_tp_native_window()
            self.stop_all()
            self.destroy()


if __name__ == "__main__":
    app = TPControlGUI()
    app.mainloop()
