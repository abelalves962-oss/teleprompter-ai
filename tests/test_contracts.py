import ast
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from rapidfuzz import fuzz


ROOT = Path(__file__).resolve().parents[1]


def load_native_scroll_harness():
    """Carrega somente os métodos de estado do TP, sem inicializar Tk/hardware."""
    source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    native = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "NativeTPWindow"
    )
    wanted = {
        "set_progress", "_log_scroll_tracking", "_advance_scroll_state", "handle_message",
        "_line_for_word", "_apply_word_target", "_state_snapshot", "manual_step",
        "_final_target_y", "_update_display_progress",
    }
    methods = [
        node for node in native.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    harness = ast.ClassDef(
        name="NativeScrollHarness",
        bases=[],
        keywords=[],
        body=methods,
        decorator_list=[],
    )
    module = ast.Module(body=[harness], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"time": time}
    exec(compile(module, "native_scroll_harness", "exec"), namespace)
    return namespace["NativeScrollHarness"]


def load_layout_functions():
    source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {"tokenize_alignment_text", "build_visual_word_map"}
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    module = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"re": re}
    exec(compile(module, "layout_functions", "exec"), namespace)
    return namespace["tokenize_alignment_text"], namespace["build_visual_word_map"]


def load_network_helpers():
    source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {"select_lan_ipv4", "build_remote_url", "mask_remote_url"}
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    module = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"ipaddress": ipaddress, "re": re}
    exec(compile(module, "network_helpers", "exec"), namespace)
    return tuple(namespace[name] for name in ("select_lan_ipv4", "build_remote_url", "mask_remote_url"))


def load_lan_transition_harness():
    source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gui = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TPControlGUI"
    )
    method = next(
        node for node in gui.body
        if isinstance(node, ast.FunctionDef) and node.name == "_enable_lan_for_qr"
    )
    harness = ast.ClassDef(
        name="LanTransitionHarness", bases=[], keywords=[], body=[method], decorator_list=[]
    )
    module = ast.Module(body=[harness], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"messagebox": object()}
    exec(compile(module, "lan_transition_harness", "exec"), namespace)
    return namespace["LanTransitionHarness"]


def load_qr_presentation_harness():
    source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gui = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TPControlGUI"
    )
    wanted = {
        "_qr_refresh", "_qr_enable_and_refresh", "_on_notebook_tab_changed",
    }
    methods = [
        node for node in gui.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    harness = ast.ClassDef(
        name="QrPresentationHarness", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    module = ast.Module(body=[harness], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"HTTP_PORT": 8000, "QR_OK": False, "build_remote_url": lambda *_: ""}
    exec(compile(module, "qr_presentation_harness", "exec"), namespace)
    return namespace["QrPresentationHarness"]


def load_native_draw_harness():
    """Carrega o desenho do HUD sem abrir uma janela Tk."""
    source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    native = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "NativeTPWindow"
    )
    draw = next(
        node for node in native.body
        if isinstance(node, ast.FunctionDef) and node.name == "draw"
    )
    harness = ast.ClassDef(
        name="NativeDrawHarness",
        bases=[],
        keywords=[],
        body=[draw],
        decorator_list=[],
    )
    module = ast.Module(body=[harness], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"PIL_NATIVE_MIRROR_OK": False}
    exec(compile(module, "native_draw_harness", "exec"), namespace)
    return namespace["NativeDrawHarness"]


def load_real_aligner():
    """Carrega a implementação real de align_spoken sem iniciar Whisper/hardware."""
    source = (ROOT / "main_align_ws.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "norm", "count_consecutive_hits", "local_match_score", "align_spoken",
        "state_snapshot", "reset_alignment_state",
    }
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    module = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "re": re, "fuzz": fuzz, "finished": False, "cursor": 0,
        "last_progress": 0.0, "stall_count": 0, "last_cursor_at_stall": 0,
        "same_cursor_count": 0, "good_match_streak": 0, "bad_match_streak": 0,
        "WINDOW_WORDS": 6, "LOCAL_BACKTRACK_WORDS": 1,
        "LOCAL_LOOKAHEAD_WORDS": 24, "LOCAL_MAX_STEP_WORDS": 12,
        "LOCAL_MIN_CONSECUTIVE_HITS": 2, "LOCAL_MIN_TOKEN_HITS": 2,
        "LOCAL_MIN_SCORE": 38, "LOCAL_STRONG_SCORE": 62,
        "sent_progress": 0.0, "last_send_ts": None, "last_reset_flag_mtime": 0.0,
        "progress_history": deque(maxlen=3), "auto_enabled": True,
        "playback_enabled": True, "ai_enabled": True, "session_id": "initial",
        "first_word_after_reset": False, "audio_lock": threading.Lock(),
        "audio_buffer": deque(),
    }
    exec(compile(module, "real_aligner", "exec"), namespace)
    text = (ROOT / "roteiro.txt").read_text(encoding="utf-8")
    namespace["script_words"] = namespace["norm"](text).split()
    namespace["N"] = len(namespace["script_words"])
    return namespace


def load_python_config_harness():
    source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    native = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TPControlGUI")
    wanted = {"_python_config_path", "_python_runs", "_resolve_python_cmd"}
    methods = [n for n in native.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    harness = ast.ClassDef(
        name="PythonConfigHarness", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    module = ast.Module(body=[harness], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Path": Path, "CONFIG_FILE": ".teleprompter_config.json", "json": json,
        "os": os, "subprocess": subprocess, "sys": sys,
    }
    exec(compile(module, "python_config_harness", "exec"), namespace)
    return namespace["PythonConfigHarness"]


class StableBuildContracts(unittest.TestCase):
    def test_python_sources_parse(self):
        for path in ROOT.glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_no_inews_runtime_code(self):
        forbidden = ("inews", "rxnet", "ftplib", "watchdog", "nsml")
        for name in ("TP_Control_GUI.py", "main_align_ws.py", "scroll_server.py"):
            text = (ROOT / name).read_text(encoding="utf-8").lower()
            for token in forbidden:
                self.assertNotIn(token, text, f"{token!r} encontrado em {name}")

    def test_network_is_loopback_by_default(self):
        server = (ROOT / "scroll_server.py").read_text(encoding="utf-8")
        gui = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        self.assertIn('else "127.0.0.1"', server)
        self.assertIn('else "127.0.0.1"', gui)
        self.assertIn("TELEPROMPTER_ALLOW_LAN", server)
        self.assertIn("TELEPROMPTER_ALLOW_LAN", gui)

    def test_reset_clears_every_progress_state(self):
        source = (ROOT / "main_align_ws.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reset_alignment_state")
        module = ast.Module(body=[fn], type_ignores=[])
        ast.fix_missing_locations(module)
        state = {
            "N": 100,
            "cursor": 88,
            "last_progress": 0.88,
            "sent_progress": 0.88,
            "last_send_ts": 123.0,
            "stall_count": 9,
            "last_cursor_at_stall": 70,
            "same_cursor_count": 4,
            "good_match_streak": 5,
            "bad_match_streak": 6,
            "progress_history": deque([0.7, 0.8, 0.88]),
            "last_reset_flag_mtime": 1.0,
            "finished": True,
            "auto_enabled": False,
            "playback_enabled": False,
            "ai_enabled": False,
            "session_id": "old",
            "first_word_after_reset": False,
            "audio_lock": threading.Lock(),
            "audio_buffer": deque([1, 2, 3]),
            "state_snapshot": lambda _label: None,
        }
        exec(compile(module, "reset_alignment_state", "exec"), state)
        state["reset_alignment_state"](0.0)
        self.assertEqual(state["cursor"], 0)
        self.assertEqual(state["last_progress"], 0.0)
        self.assertEqual(state["sent_progress"], 0.0)
        self.assertIsNone(state["last_send_ts"])
        self.assertEqual(list(state["progress_history"]), [])
        self.assertEqual(state["stall_count"], 0)
        self.assertEqual(state["same_cursor_count"], 0)
        self.assertEqual(state["good_match_streak"], 0)
        self.assertEqual(state["bad_match_streak"], 0)
        self.assertFalse(state["finished"])
        self.assertTrue(state["auto_enabled"])
        self.assertTrue(state["playback_enabled"])
        self.assertTrue(state["ai_enabled"])
        self.assertEqual(list(state["audio_buffer"]), [])

    def test_operating_mode_contracts_are_present(self):
        gui = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        self.assertIn("if not self.playing", gui)
        self.assertIn("self.finished = line_after >= line_count", gui)
        self.assertIn("self._scroll_vel_px = 0.0", gui)
        self.assertIn("accel = diff_px * 6.5", gui)
        self.assertNotIn('if mode == "auto":\n                self.auto_mode = True\n                self.playing = True', gui)

    def test_mode_sender_is_persistent_and_instrumented(self):
        gui = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        self.assertIn("GUIControlWebSocket", gui)
        self.assertIn("_control_ws_queue.put(ws_payload)", gui)
        self.assertIn("async def _control_ws_loop", gui)
        self.assertNotIn('subprocess.run(\n                [self.python_cmd.get().strip(), "-c", code, msg]', gui)
        self.assertIn("t_native_receive", gui)
        self.assertIn("t_native_state", gui)
        self.assertIn("primeiro render", gui)
        self.assertIn("MODO: AUTO", gui)

    def test_project_python_is_persisted_and_restored(self):
        expected = str((ROOT / ".venv" / "Scripts" / "python.exe").resolve())
        self.assertTrue(os.path.isfile(expected))
        config = json.loads((ROOT / ".teleprompter_config.json").read_text(encoding="utf-8"))
        self.assertEqual(os.path.abspath(config["python_exe"]), expected)
        cls = load_python_config_harness()
        first = cls()
        first.project_dir = ROOT
        second = cls()
        second.project_dir = ROOT
        self.assertEqual(first._resolve_python_cmd(), (expected, "saved"))
        self.assertEqual(second._resolve_python_cmd(), (expected, "saved"))

    def test_real_aligner_accepts_current_script_opening(self):
        state = load_real_aligner()
        first = state["align_spoken"](
            "Bem-vindos a esta demonstração do Teleprompter com Inteligência Artificial.",
            pipe_id="test-1",
        )
        self.assertIsNotNone(first)
        self.assertFalse(first["blocked"])
        self.assertEqual(state["cursor"], 6)
        self.assertEqual(first["word_index"], 6)
        self.assertAlmostEqual(first["progress"], 0.045)

        second = state["align_spoken"](
            "O sistema acompanha a leitura em português e atualiza a posição do texto de forma progressiva.",
            pipe_id="test-2",
        )
        self.assertIsNotNone(second)
        self.assertFalse(second["blocked"])
        self.assertEqual(state["cursor"], 18)
        self.assertEqual(second["word_index"], 18)
        self.assertAlmostEqual(second["progress"], 0.090)

        state["reset_alignment_state"](0.0, "second-session")
        replay = state["align_spoken"](
            "Bem-vindos a esta demonstração do Teleprompter com Inteligência Artificial.",
            pipe_id="test-reset",
        )
        self.assertFalse(replay["blocked"])
        self.assertEqual(replay["word_index"], 6)


class NativeScrollTargetContracts(unittest.TestCase):
    def make_tp(self, current):
        cls = load_native_scroll_harness()
        tp = cls()
        tp.progress = current
        tp.target_progress = current
        tp.pending_progress = current
        tp.last_received_progress = current
        tp.last_accepted_progress = current
        tp.last_word_index = None
        tp.last_word_count = 0
        tp.target_line_index = None
        tp.display_progress = current
        tp.session_id = "initial"
        tp.first_scroll_after_reset = False
        tp.finished = False
        tp.playing = True
        tp.auto_mode = True
        tp.ignore_ai_scroll_until = 0.0
        tp.total_height = 5000
        tp.line_height = 90
        tp.lines = [f"linha {i}" for i in range(57)]
        tp.line_word_ranges = [(i, i) for i in range(57)]
        tp._scroll_vel_px = 0.0
        tp._last_scroll_trace_ts = 0.0
        tp._last_event_id = None
        tp.logs = []
        tp.log = tp.logs.append
        tp.draw = lambda: None
        tp.reload_script = lambda: setattr(tp, "_scroll_vel_px", 0.0)
        return tp

    def advance_to_target(self, tp, frames=5000):
        for _ in range(frames):
            tp._advance_scroll_state(0.016)
            if abs(tp.progress - tp.target_progress) < 0.000001:
                break
        self.assertAlmostEqual(tp.progress, tp.target_progress, delta=0.0001)

    def test_large_targets_are_preserved_without_more_ai_messages(self):
        for current, received in ((0.30, 0.60), (0.10, 0.80), (0.40, 0.45)):
            with self.subTest(current=current, received=received):
                tp = self.make_tp(current)
                tp.set_progress(received)
                self.assertEqual(tp.progress, current)
                self.assertEqual(tp.target_progress, received)
                self.assertEqual(tp.pending_progress, received)
                self.advance_to_target(tp)

    def test_pause_and_play_preserve_pending_target(self):
        tp = self.make_tp(0.30)
        tp.set_progress(0.60)
        tp.handle_message({"type": "control", "action": "pause"})
        frozen = tp.progress
        tp.handle_message({"type": "scroll", "progress": 0.70})
        for _ in range(100):
            tp._advance_scroll_state(0.016)
        self.assertEqual(tp.progress, frozen)
        self.assertEqual(tp.pending_progress, 0.70)
        tp.handle_message({"type": "control", "action": "play"})
        self.advance_to_target(tp)

    def test_manual_and_auto_preserve_pending_target_without_jump(self):
        tp = self.make_tp(0.30)
        tp.set_progress(0.60)
        for _ in range(20):
            tp._advance_scroll_state(0.016)
        tp.handle_message({"type": "control", "mode": "manual"})
        frozen = tp.progress
        tp.handle_message({"type": "scroll", "progress": 0.70, "word_index": 40, "word_count": 57})
        for _ in range(100):
            tp._advance_scroll_state(0.016)
        self.assertEqual(tp.progress, frozen)
        self.assertEqual(tp.pending_progress, 0.70)
        tp.handle_message({"type": "control", "mode": "auto"})
        self.assertEqual(tp.progress, frozen)
        self.assertEqual(tp.target_line_index, 40)
        self.assertAlmostEqual(tp.target_progress, 40 / 56)
        self.advance_to_target(tp)

    def test_reset_clears_current_target_and_pending(self):
        tp = self.make_tp(0.30)
        tp.set_progress(0.60)
        tp._scroll_vel_px = 50.0
        tp.handle_message({"type": "control", "action": "reset"})
        self.assertEqual(tp.progress, 0.0)
        self.assertEqual(tp.target_progress, 0.0)
        self.assertEqual(tp.pending_progress, 0.0)
        self.assertEqual(tp.last_received_progress, 0.0)
        self.assertIsNone(tp.last_word_index)
        self.assertEqual(tp.last_word_count, 0)
        self.assertEqual(tp._scroll_vel_px, 0.0)
        self.assertFalse(tp.finished)

    def test_finished_reaches_and_holds_one(self):
        tp = self.make_tp(0.80)
        tp.set_progress(1.0)
        self.advance_to_target(tp)
        self.assertEqual(tp.progress, 1.0)
        self.assertEqual(tp.target_progress, 1.0)
        self.assertEqual(tp.pending_progress, 1.0)
        self.assertTrue(tp.finished)

    def test_word_index_is_primary_and_maps_boundaries(self):
        tp = self.make_tp(0.0)
        tp.lines = ["um dois três", "quatro cinco", "seis"]
        tp.line_word_ranges = [(0, 2), (3, 4), (5, 5)]
        for word_index, expected_line, expected_y in (
            (0, 0, 0.0), (1, 0, 30.0), (3, 1, 90.0), (5, 2, 180.0),
        ):
            with self.subTest(word_index=word_index):
                tp.progress = 0.0
                tp.target_progress = 0.0
                tp.set_progress(word_index / 5, word_index, 6)
                self.assertEqual(tp.target_line_index, expected_line)
                self.assertAlmostEqual(tp.target_progress * tp._final_target_y(), expected_y)

    def make_real_word_map_tp(self):
        tp = self.make_tp(0.0)
        text = "zero um dois três quatro cinco seis sete oito nove"
        _tokenize, build_word_map = load_layout_functions()
        tp.lines, tp.line_word_ranges = build_word_map(
            text, 400, lambda value: len(value.split()) * 100
        )
        tp.total_height = len(tp.lines) * tp.line_height
        self.assertEqual(tp.line_word_ranges, [(0, 3), (4, 7), (8, 9)])
        return tp

    def target_y_for_word(self, tp, word_index, progress=None):
        if progress is None:
            progress = word_index / 9
        tp.set_progress(progress, word_index, 10)
        return tp.target_progress * tp._final_target_y()

    def test_word_interpolation_moves_within_first_visual_line(self):
        tp = self.make_real_word_map_tp()
        start_y = self.target_y_for_word(tp, 0)
        middle_y = self.target_y_for_word(tp, 2)
        near_end_y = self.target_y_for_word(tp, 3)
        self.assertEqual(start_y, 0.0)
        self.assertGreater(middle_y, 0.0)
        self.assertLess(middle_y, tp.line_height)
        self.assertGreater(near_end_y, middle_y)
        self.assertLess(near_end_y, tp.line_height)

    def test_word_interpolation_transition_does_not_regress_or_jump(self):
        tp = self.make_real_word_map_tp()
        last_line_zero_y = self.target_y_for_word(tp, 3)
        first_line_one_y = self.target_y_for_word(tp, 4)
        self.assertGreaterEqual(first_line_one_y, last_line_zero_y)
        self.assertLessEqual(first_line_one_y - last_line_zero_y, tp.line_height)
        self.assertEqual(first_line_one_y, tp.line_height)

    def test_word_interpolation_increases_inside_intermediate_line(self):
        tp = self.make_real_word_map_tp()
        targets = [self.target_y_for_word(tp, index) for index in (4, 5, 6, 7)]
        self.assertEqual(targets, sorted(targets))
        self.assertEqual(len(targets), len(set(targets)))

    def test_manual_steps_remain_exactly_line_based_with_word_map(self):
        tp = self.make_real_word_map_tp()
        tp.handle_message({"type": "control", "mode": "manual"})
        tp.target_line_index = 0
        tp.target_progress = 0.0
        tp.handle_message({"type": "control", "action": "forward", "value": 0.01})
        self.assertEqual(tp.target_line_index, 1)
        self.assertEqual(tp.target_progress * tp._final_target_y(), tp.line_height)
        tp.handle_message({"type": "control", "action": "back", "value": 0.01})
        self.assertEqual(tp.target_line_index, 0)
        self.assertEqual(tp.target_progress, 0.0)

    def test_reset_then_first_line_words_create_small_targets(self):
        tp = self.make_real_word_map_tp()
        tp.progress = 0.5
        tp.target_progress = 0.5
        tp.last_word_index = 5
        tp.handle_message({"type": "control", "action": "reset", "session_id": "reset-map"})
        self.assertEqual(tp.progress, 0.0)
        self.assertEqual(tp.target_progress, 0.0)
        self.assertIsNone(tp.last_word_index)
        first_target = self.target_y_for_word(tp, 1, progress=0.01)
        second_target = self.target_y_for_word(tp, 2, progress=0.02)
        self.assertGreater(first_target, 0.0)
        self.assertGreater(second_target, first_target)
        self.assertTrue(any(line.startswith("WORD_INTERPOLATION word_index=1") for line in tp.logs))

    def test_word_interpolation_final_reaches_exact_visual_end(self):
        tp = self.make_real_word_map_tp()
        self.target_y_for_word(tp, 9, progress=1.0)
        self.assertEqual(tp.target_progress, 1.0)
        self.advance_to_target(tp)
        self.assertEqual(tp.progress, 1.0)
        self.assertEqual(tp.display_progress, 1.0)
        self.assertTrue(tp.finished)

    def test_word_index_jump_uses_one_preserved_target(self):
        tp = self.make_tp(0.0)
        tp.lines = ["a", "b", "c", "d"]
        tp.line_word_ranges = [(0, 1), (2, 3), (4, 5), (6, 7)]
        tp.total_height = 360
        tp.set_progress(6 / 7, 6, 8)
        self.assertEqual(tp.target_line_index, 3)
        self.assertEqual(tp.progress, 0.0)
        self.advance_to_target(tp)

    def test_pixel_target_runs_without_new_websocket_messages(self):
        tp = self.make_tp(0.0)
        visual_height = (len(tp.lines) - 1) * tp.line_height
        tp.target_progress = 300.0 / visual_height
        frames = []
        for _ in range(3):
            tp._advance_scroll_state(0.016)
            frames.append(tp.progress * visual_height)
        self.assertGreater(frames[0], 0.0)
        self.assertGreater(frames[1], frames[0])
        self.assertGreater(frames[2], frames[1])
        self.advance_to_target(tp)
        self.assertAlmostEqual(tp.progress * visual_height, 300.0, delta=0.5)

        tp.target_progress = 800.0 / visual_height
        before = tp.progress * visual_height
        for _ in range(3):
            tp._advance_scroll_state(0.016)
        self.assertGreater(tp.progress * visual_height, before)

        tp.playing = False
        frozen = tp.progress
        for _ in range(20):
            tp._advance_scroll_state(0.016)
        self.assertEqual(tp.progress, frozen)
        tp.playing = True
        tp._advance_scroll_state(0.016)
        self.assertGreater(tp.progress, frozen)

    def test_render_loop_keeps_scheduling_after_motor_frame(self):
        source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        native = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NativeTPWindow")
        render = next(n for n in native.body if isinstance(n, ast.FunctionDef) and n.name == "render_loop")
        render_source = ast.get_source_segment(source, render)
        self.assertIn("self._advance_scroll_state(dt)", render_source)
        self.assertIn("self.after(self.render_interval_ms, self.render_loop)", render_source)
        self.assertNotIn("self.draw()\n        return True", render_source)

    def test_native_hud_redraws_when_only_auto_mode_changes(self):
        source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        native = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NativeTPWindow")
        draw = next(n for n in native.body if isinstance(n, ast.FunctionDef) and n.name == "draw")
        draw_source = ast.get_source_segment(source, draw)

        self.assertIn('mode_text = "AUTO" if self.auto_mode else "MANUAL"', draw_source)
        self.assertIn('hud_text = f"{mode_text} ► {display_percent}%"', draw_source)
        self.assertIn('self._last_draw_auto_mode = self.auto_mode', draw_source)
        self.assertIn('HUD_RENDER auto_mode=', draw_source)

    def test_native_hud_follows_auto_manual_steps_auto_and_reset(self):
        class FakeCanvas:
            def __init__(self):
                self.texts = []

            def winfo_width(self): return 1280
            def winfo_height(self): return 720
            def delete(self, _tag): self.texts = []
            def create_rectangle(self, *_args, **_kwargs): pass
            def create_polygon(self, *_args, **_kwargs): pass
            def create_text(self, *_args, **kwargs): self.texts.append(kwargs.get("text"))

        tp = load_native_draw_harness()()
        tp.canvas = FakeCanvas()
        tp.mirror = False
        tp.auto_mode = True
        tp.progress = 0.35
        tp.display_progress = 0.35
        tp.lines = []
        tp.side_margin = 100
        tp.text_align_left = True
        tp.font_family = "Arial"
        tp.font_size = 56
        tp.line_height = 78
        tp._last_draw_progress = -1.0
        tp._last_draw_size = (0, 0)
        tp._last_draw_auto_mode = None
        tp._scene_positions = lambda _w, _h: (200, 0)
        tp._font_tuple = lambda: ("Arial", 56, "bold")
        tp.log = lambda _message: None

        tp.draw()
        self.assertIn("AUTO ► 35%", tp.canvas.texts)

        tp.auto_mode = False
        tp.draw()
        self.assertIn("MANUAL ► 35%", tp.canvas.texts)

        for progress in (0.36, 0.35):  # Avançar e Voltar preservam MANUAL.
            tp.progress = progress
            tp.display_progress = progress
            tp.draw()
            self.assertIn(f"MANUAL ► {int(progress * 100)}%", tp.canvas.texts)

        tp.auto_mode = True
        tp.draw()
        self.assertIn("AUTO ► 35%", tp.canvas.texts)

        tp.progress = 0.0
        tp.display_progress = 0.0
        tp.draw()
        self.assertIn("AUTO ► 0%", tp.canvas.texts)

    def test_auto_tracks_increasing_word_indexes(self):
        tp = self.make_tp(0.0)
        observed_targets = []
        for word_index in (2, 8, 15, 30):
            tp.set_progress(word_index / 56, word_index, 57)
            observed_targets.append(tp.target_progress)
        self.assertEqual(observed_targets, sorted(observed_targets))
        self.assertEqual(tp.target_line_index, 30)

    def test_reload_contract_rebuilds_map_and_resets_word_state(self):
        source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        native = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NativeTPWindow")
        reload_fn = next(n for n in native.body if isinstance(n, ast.FunctionDef) and n.name == "reload_script")
        reload_source = ast.get_source_segment(source, reload_fn)
        self.assertIn("self.wrap_text_to_lines(text)", reload_source)
        self.assertIn("self.last_word_index = None", reload_source)
        self.assertIn("self.target_line_index = None", reload_source)

    def finish_session(self, tp, session):
        last = len(tp.lines) - 1
        tp.handle_message({
            "type": "scroll", "progress": 1.0, "word_index": last,
            "word_count": len(tp.lines), "session_id": session,
        })
        self.advance_to_target(tp)
        self.assertTrue(tp.finished)

    def test_two_complete_sessions_without_process_restart(self):
        tp = self.make_tp(0.0)
        self.finish_session(tp, "initial")
        tp.handle_message({"type": "control", "action": "reset", "session_id": "session-2"})
        self.assertFalse(tp.finished)
        self.assertEqual(tp.progress, 0.0)

        # Pacote atrasado da sessão 1 não pode contaminar os estados monotônicos.
        tp.handle_message({
            "type": "scroll", "progress": 1.0, "word_index": 56,
            "word_count": 57, "session_id": "initial",
        })
        self.assertEqual(tp.last_received_progress, 0.0)

        tp.handle_message({
            "type": "scroll", "progress": 0.04, "word_index": 2,
            "word_count": 57, "session_id": "session-2",
        })
        self.assertEqual(tp.last_word_index, 2)
        self.finish_session(tp, "session-2")

    def test_finished_reset_manual_auto_then_second_session(self):
        tp = self.make_tp(0.0)
        self.finish_session(tp, "initial")
        tp.handle_message({"type": "control", "action": "reset", "session_id": "session-2"})
        tp.handle_message({"type": "control", "mode": "manual"})
        self.assertFalse(tp.auto_mode)
        self.assertTrue(tp.manual_step(0.01))
        manual_position = tp.progress
        tp.handle_message({
            "type": "scroll", "progress": 0.20, "word_index": 11,
            "word_count": 57, "session_id": "session-2",
        })
        self.assertEqual(tp.progress, manual_position)
        tp.handle_message({"type": "control", "mode": "auto"})
        self.assertTrue(tp.auto_mode)
        self.assertEqual(tp.progress, manual_position)
        self.finish_session(tp, "session-2")

    def test_partial_pause_manual_and_auto_resets_restore_initial_mode(self):
        for mode, playing in (("auto", True), ("manual", True), ("auto", False)):
            with self.subTest(mode=mode, playing=playing):
                tp = self.make_tp(0.25)
                tp.auto_mode = mode == "auto"
                tp.playing = playing
                tp.finished = False
                tp.handle_message({"type": "control", "action": "reset", "session_id": "new"})
                self.assertTrue(tp.auto_mode)
                self.assertTrue(tp.playing)
                self.assertFalse(tp.finished)
                self.assertEqual(tp.progress, 0.0)

    def test_forward_does_not_silently_change_auto_mode(self):
        tp = self.make_tp(0.20)
        tp.handle_message({"type": "control", "action": "forward", "value": 0.01})
        self.assertTrue(tp.auto_mode)
        self.assertEqual(tp.progress, 0.20)
        tp.handle_message({"type": "control", "mode": "manual"})
        tp.handle_message({"type": "control", "action": "forward", "value": 0.01})
        self.assertFalse(tp.auto_mode)
        self.assertAlmostEqual(tp.progress, 0.20)
        self.assertEqual(tp.target_line_index, 12)

    def test_manual_three_forward_two_back_then_auto(self):
        tp = self.make_tp(0.20)
        tp.handle_message({"type": "control", "mode": "manual"})
        start_line = tp.target_line_index
        for _ in range(3):
            tp.handle_message({"type": "control", "action": "forward", "value": 0.01})
        self.assertFalse(tp.auto_mode)
        self.assertEqual(tp.target_line_index, start_line + 3)
        for _ in range(2):
            tp.handle_message({"type": "control", "action": "back", "value": 0.01})
        self.assertEqual(tp.target_line_index, start_line + 1)
        self.assertEqual(tp.progress, 0.20)
        tp._advance_scroll_state(0.016)
        self.assertGreater(tp.progress, 0.0)
        tp.handle_message({"type": "control", "mode": "auto"})
        self.assertTrue(tp.auto_mode)

    def test_manual_back_moves_visual_position_downward(self):
        tp = self.make_tp(0.50)
        tp.handle_message({"type": "control", "mode": "manual"})
        tp.handle_message({"type": "control", "action": "back", "value": 0.01})
        before = tp.progress
        for _ in range(20):
            tp._advance_scroll_state(0.016)
        self.assertLess(tp.progress, before)
        self.assertFalse(tp.auto_mode)

    def test_control_event_id_is_applied_once(self):
        tp = self.make_tp(0.20)
        tp.handle_message({"type": "control", "mode": "manual", "event_id": "mode-1"})
        start_line = tp.target_line_index
        command = {
            "type": "control", "action": "forward", "value": 0.01,
            "event_id": "forward-1", "_source": "gui",
        }
        tp.handle_message(dict(command))
        tp.handle_message(dict(command))
        self.assertEqual(tp.target_line_index, start_line + 1)
        self.assertTrue(any("CONTROL_EVENT event_id=forward-1" in line and "duplicate=True" in line
                            for line in tp.logs))

    def test_remote_event_id_remains_deduplicated_after_an_interleaved_event(self):
        tp = self.make_tp(0.20)
        tp.handle_message({
            "type": "control", "mode": "manual",
            "event_id": "remote-manual-interleaved", "_source": "remote",
        })
        start_line = tp.target_line_index
        forward = {
            "type": "control", "action": "forward", "value": 0.01,
            "event_id": "remote-forward-interleaved", "_source": "remote",
        }
        tp.handle_message(dict(forward))
        tp.handle_message({
            "type": "control", "action": "pause",
            "event_id": "remote-pause-interleaved", "_source": "remote",
        })
        tp.handle_message(dict(forward))
        self.assertEqual(tp.target_line_index, start_line + 1)

    def test_remote_mirror_click_toggles_exactly_once(self):
        tp = self.make_tp(0.20)
        tp.mirror = False
        toggle_count = 0

        def toggle_mirror():
            nonlocal toggle_count
            toggle_count += 1
            tp.mirror = not tp.mirror

        tp.toggle_mirror = toggle_mirror
        first = {
            "type": "control", "action": "toggle_mirror",
            "event_id": "remote-mirror-1", "_source": "remote",
        }
        tp.handle_message(dict(first))
        tp.handle_message(dict(first))
        self.assertTrue(tp.mirror)
        self.assertEqual(toggle_count, 1)

        tp.handle_message({
            "type": "control", "action": "toggle_mirror",
            "event_id": "remote-mirror-2", "_source": "remote",
        })
        self.assertFalse(tp.mirror)
        self.assertEqual(toggle_count, 2)

    def test_remote_forward_and_back_are_each_applied_once(self):
        tp = self.make_tp(0.20)
        tp.handle_message({
            "type": "control", "mode": "manual",
            "event_id": "remote-manual-1", "_source": "remote",
        })
        start_line = tp.target_line_index
        for action in ("forward", "back"):
            command = {
                "type": "control", "action": action, "value": 0.01,
                "event_id": f"remote-{action}-1", "_source": "remote",
            }
            tp.handle_message(dict(command))
            tp.handle_message(dict(command))
        self.assertEqual(tp.target_line_index, start_line)

    def test_remote_play_pause_event_is_applied_once(self):
        tp = self.make_tp(0.20)
        toggle_count = 0

        def toggle_play():
            nonlocal toggle_count
            toggle_count += 1
            tp.playing = not tp.playing

        tp.toggle_play = toggle_play
        command = {
            "type": "control", "action": "toggle_play",
            "event_id": "remote-play-pause-1", "_source": "remote",
        }
        tp.handle_message(dict(command))
        tp.handle_message(dict(command))
        self.assertFalse(tp.playing)
        self.assertEqual(toggle_count, 1)

    def test_manual_motor_emits_manual_trace_and_moves_target(self):
        tp = self.make_tp(0.0)
        tp.handle_message({"type": "control", "mode": "manual"})
        tp.handle_message({"type": "control", "action": "forward", "value": 0.01})
        tp._motor_debug_frames = 2
        for _ in range(20):
            tp._advance_scroll_state(0.016)
        self.assertGreater(tp.progress, 0.0)
        self.assertTrue(any(line.startswith("[MOTOR_MANUAL_ENTRY]") for line in tp.logs))
        self.assertTrue(any(line.startswith("[MOTOR_MANUAL_STEP]") for line in tp.logs))

    def test_display_progress_uses_physical_position_not_ai_progress(self):
        tp = self.make_tp(0.0)
        tp.last_received_progress = 0.746
        tp.progress = 2278 / ((len(tp.lines) - 1) * tp.line_height)
        tp._update_display_progress()
        self.assertAlmostEqual(tp.display_progress, 2278 / ((len(tp.lines) - 1) * tp.line_height))
        tp.progress = 0.5
        tp._update_display_progress()
        self.assertAlmostEqual(tp.display_progress, 0.5)
        tp.progress = 1.0
        tp._update_display_progress()
        self.assertEqual(tp.display_progress, 1.0)


class NetworkAccessContracts(unittest.TestCase):
    def setUp(self):
        self.select_ip, self.build_url, self.mask_url = load_network_helpers()

    def make_qr_presentation(self, ip, lan_enabled=False, error=None):
        class Var:
            def __init__(self, value=""): self.value = value
            def set(self, value): self.value = value
            def get(self): return self.value

        class Label:
            def __init__(self): self.options = {}
            def configure(self, **kwargs): self.options.update(kwargs)

        class Process:
            popen = None

        gui = load_qr_presentation_harness()()
        gui.lan_access_enabled = lan_enabled
        gui._lan_enable_error = error
        gui.http_running = False
        gui.ws_proc = Process()
        gui._get_local_ip = lambda: ip
        gui._qr_ip_var = Var()
        gui._qr_url_remote_var = Var()
        gui._qr_url_tp_var = Var()
        gui._pair_network_var = Var()
        gui._pair_hint_var = Var()
        gui._pair_http_var = Var()
        gui._pair_ws_var = Var()
        gui._pair_remote_var = Var()
        gui.remote_pin_var = Var("1234")
        gui._qr_label = Label()
        gui._pair_network_lbl = Label()
        gui._pair_http_lbl = Label()
        gui._pair_ws_lbl = Label()
        gui._pair_remote_lbl = Label()
        gui._set_status_label_style = lambda *_args: None
        gui._read_tp_status = lambda: {}
        gui._native_tp_is_connected = lambda: False
        gui._write_remote_by_page = lambda: None
        return gui

    def test_loopback_remains_the_default_without_allow_lan(self):
        gui = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        server = (ROOT / "scroll_server.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("TELEPROMPTER_ALLOW_LAN", "0") == "1"', gui)
        self.assertIn('HTTP_HOST = "0.0.0.0" if ALLOW_LAN else "127.0.0.1"', gui)
        self.assertIn('else "127.0.0.1"', server)

    def test_physical_wifi_is_selected(self):
        interfaces = [
            ("Ethernet", True, ["10.0.0.20"]),
            ("Wi-Fi", True, ["192.168.50.25"]),
        ]
        self.assertEqual(self.select_ip(interfaces), "192.168.50.25")

    def test_loopback_link_local_and_virtual_interfaces_are_rejected(self):
        interfaces = [
            ("Loopback Pseudo-Interface", True, ["127.0.0.1"]),
            ("Conexão Local", True, ["169.254.10.20"]),
            ("vEthernet (Default Switch)", True, ["172.17.112.1"]),
            ("Wi-Fi", True, ["192.168.50.25"]),
        ]
        self.assertEqual(self.select_ip(interfaces), "192.168.50.25")

    def test_remote_url_uses_detected_lan_ip(self):
        url = self.build_url("192.168.50.25", 8000, "1234")
        self.assertEqual(url, "http://192.168.50.25:8000/remote_by.html?pin=1234")

    def test_remote_url_never_accepts_wildcard(self):
        with self.assertRaises(ValueError):
            self.build_url("0.0.0.0", 8000, "1234")

    def test_lan_remote_url_never_uses_loopback(self):
        with self.assertRaises(ValueError):
            self.build_url("127.0.0.1", 8000, "1234")

    def test_pin_is_masked_in_logged_url(self):
        masked = self.mask_url("http://10.0.0.2:8000/remote_by.html?pin=7391")
        self.assertNotIn("7391", masked)
        self.assertIn("pin=****", masked)
        gui = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        self.assertNotIn('self.log(f"Novo PIN de pareamento: {self.remote_pin_var.get()}")', gui)

    def test_network_restart_preserves_teleprompter_operational_state(self):
        class Process:
            def poll(self): return None

        class Managed:
            popen = Process()

        tp = load_lan_transition_harness()()
        tp.auto_mode = False
        tp.playing = False
        tp.current_y = 321.5
        tp.target_y = 456.0
        tp.session_id = "session-preserved"
        before = (tp.auto_mode, tp.playing, tp.current_y, tp.target_y, tp.session_id)
        tp.ws_proc = Managed()
        tp.http_running = True
        tp._http_bind_host = "127.0.0.1"
        tp._ws_lan_enabled = False
        tp.lan_access_enabled = False
        tp.logs = []
        tp.log = tp.logs.append
        tp._get_local_ip = lambda: "192.168.50.25"

        def stop_http():
            tp.http_running = False
            tp._http_bind_host = None

        def stop_ws():
            tp.ws_proc.popen = None
            tp._ws_lan_enabled = None

        def start_http():
            tp.http_running = True
            tp._http_bind_host = "0.0.0.0"
            return True

        def start_ws():
            tp.ws_proc.popen = Process()
            tp._ws_lan_enabled = True
            return True

        tp.stop_http = stop_http
        tp.stop_ws = stop_ws
        tp.start_http = start_http
        tp.start_ws = start_ws

        self.assertTrue(tp._enable_lan_for_qr())
        self.assertEqual(
            (tp.auto_mode, tp.playing, tp.current_y, tp.target_y, tp.session_id), before
        )
        self.assertEqual(tp._http_bind_host, "0.0.0.0")
        self.assertTrue(tp._ws_lan_enabled)

    def test_detected_ip_is_visible_while_lan_is_disabled(self):
        gui = self.make_qr_presentation("10.20.30.40")
        gui._qr_refresh()
        self.assertEqual(gui._qr_ip_var.get(), "10.20.30.40")
        self.assertEqual(gui._pair_network_var.get(), "Rede: acesso LAN ainda não habilitado")

    def test_detected_ip_does_not_show_unavailable_message(self):
        gui = self.make_qr_presentation("10.20.30.40")
        gui._qr_refresh()
        self.assertNotEqual(gui._qr_ip_var.get(), "IPv4 LAN não disponível")
        self.assertIn("Acesso LAN ainda não habilitado", gui._qr_label.options["text"])

    def test_missing_ip_shows_lan_unavailable(self):
        gui = self.make_qr_presentation(None)
        gui._qr_refresh()
        self.assertEqual(gui._qr_ip_var.get(), "IPv4 LAN não disponível")
        self.assertEqual(
            gui._qr_label.options["text"], "QR indisponível sem uma interface LAN ativa."
        )

    def test_update_ip_qr_action_explicitly_promotes_lan(self):
        gui = self.make_qr_presentation("10.20.30.40")
        calls = []
        gui._enable_lan_for_qr = lambda: calls.append("enable") or True
        gui._qr_refresh = lambda: calls.append("refresh") or True
        self.assertTrue(gui._qr_enable_and_refresh())
        self.assertEqual(calls, ["enable", "refresh"])

    def test_periodic_refresh_never_promotes_lan(self):
        source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        gui = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TPControlGUI")
        refresh_loop = next(
            n for n in gui.body
            if isinstance(n, ast.FunctionDef) and n.name == "_refresh_status_loop"
        )
        method_source = ast.get_source_segment(source, refresh_loop)
        self.assertIn("self._qr_refresh()", method_source)
        self.assertNotIn("_enable_lan_for_qr", method_source)
        self.assertNotIn("_qr_enable_and_refresh", method_source)

    def test_entering_pairing_tab_promotes_lan_and_refreshes_qr(self):
        gui = self.make_qr_presentation("10.20.30.40")
        calls = []

        class Notebook:
            def select(self): return "pairing-tab"

        gui.notebook = Notebook()
        gui._tab_qr = "pairing-tab"
        gui._qr_enable_and_refresh = lambda: calls.append("enable-and-refresh")
        gui._on_notebook_tab_changed()
        self.assertEqual(calls, ["enable-and-refresh"])

    def test_remote_page_assigns_unique_event_id_and_source(self):
        source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        self.assertIn('payload.event_id = "remote-" + Date.now() + "-" + eventSequence', source)
        self.assertIn('payload._source = "remote"', source)

    def test_websocket_stdout_is_not_redispatched_to_native_tp(self):
        source = (ROOT / "TP_Control_GUI.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        gui = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TPControlGUI")
        reader = next(
            n for n in gui.body
            if isinstance(n, ast.FunctionDef) and n.name == "_read_process_output"
        )
        method_source = ast.get_source_segment(source, reader)
        self.assertNotIn("_bridge_ws_line_to_native", method_source)

    def test_http_failure_keeps_ip_visible_and_reports_http_error(self):
        gui = self.make_qr_presentation("10.20.30.40", error="http")
        gui._qr_refresh()
        self.assertEqual(gui._qr_ip_var.get(), "10.20.30.40")
        self.assertEqual(gui._pair_http_var.get(), "HTTP LAN: falha")
        self.assertEqual(gui._pair_network_var.get(), "Rede: promoção LAN falhou")

    def test_websocket_failure_keeps_ip_visible_and_reports_ws_error(self):
        gui = self.make_qr_presentation("10.20.30.40", error="websocket")
        gui._qr_refresh()
        self.assertEqual(gui._qr_ip_var.get(), "10.20.30.40")
        self.assertEqual(gui._pair_ws_var.get(), "WebSocket LAN: falha")
        self.assertEqual(gui._pair_network_var.get(), "Rede: promoção LAN falhou")


class VisualWordMapContracts(unittest.TestCase):
    def setUp(self):
        self.tokenize, self.wrap = load_layout_functions()

    def test_tokenization_matches_alignment_rules(self):
        text = "[[PAUTA]] INTERNA\nOlá, mundo!\n// COMANDO //\nAção técnico-científica"
        self.assertEqual(self.tokenize(text), ["olá", "mundo", "ação", "técnico", "científica"])

    def test_width_and_font_rebuild_word_ranges(self):
        text = "um dois três quatro cinco seis sete oito nove"
        wide_lines, wide_ranges = self.wrap(text, 1000, lambda value: len(value) * 10)
        narrow_lines, narrow_ranges = self.wrap(text, 100, lambda value: len(value) * 10)
        large_font_lines, _ = self.wrap(text, 200, lambda value: len(value) * 20)
        self.assertEqual(len(wide_lines), 1)
        self.assertGreaterEqual(len(narrow_lines), 3)
        self.assertGreater(len(large_font_lines), len(wide_lines))
        self.assertEqual(wide_ranges[0], (0, 8))
        self.assertEqual(narrow_ranges[-1][1], 8)

    def test_demo_percent_and_word_line_positions_are_measurably_different(self):
        text = (ROOT / "examples" / "roteiro_demo.txt").read_text(encoding="utf-8")
        lines, ranges = self.wrap(text, 900, lambda value: len(value) * 31)
        word_count = len(self.tokenize(text))
        line_height = 100
        errors = []
        for word_index in (word_count // 4, word_count // 2, (word_count * 3) // 4):
            line_index = next(i for i, item in enumerate(ranges) if item and item[0] <= word_index <= item[1])
            percent_y = (word_index / max(1, word_count - 1)) * max(1, len(lines) - 1) * line_height
            word_y = line_index * line_height
            errors.append(abs(percent_y - word_y))
        self.assertTrue(any(error > 1.0 for error in errors))

    def test_aligner_exposes_word_index_without_removing_progress(self):
        source = (ROOT / "main_align_ws.py").read_text(encoding="utf-8")
        self.assertIn('"progress": float(progress)', source)
        self.assertIn('"word_index": int(result.get("word_index", cursor))', source)
        self.assertIn('"word_count": int(result.get("word_count", N))', source)


if __name__ == "__main__":
    unittest.main()
