# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""Voice input: hold or toggle a hotkey to record, transcribe, and paste."""

import json
import threading
import ctypes
import time
from ctypes import wintypes
from pathlib import Path

import keyboard
import pyperclip
import pystray
from PIL import Image, ImageDraw
from PyQt5 import QtCore, QtGui, QtWidgets

from recorder import Recorder
from transcriber import make_transcriber

# ---- Config -----------------------------------------------------------------

_DEFAULT_CONFIG = """{
    "provider": "siliconflow",

    "siliconflow_api_key": "",
    "volcengine_api_key": "",

    "hotkey": "ctrl+alt+v",
    "toggle_hotkey": "ctrl+shift+r",
    "paste_after": true,
    "use_system_proxy": false
}
"""


def _app_dir() -> Path:
    """Folder to read config / write logs from.

    When frozen by PyInstaller, files live next to the .exe, not in the
    temp extraction dir, so use the executable's folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


_cfg_path = _app_dir() / "config.json"

if not _cfg_path.exists():
    # First run (typical for the .exe): create a blank config next to us,
    # open it in Notepad, and tell the user to fill in the API key.
    _cfg_path.write_text(_DEFAULT_CONFIG, encoding="utf-8")
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "首次使用：已生成 config.json，请填入你的 API Key 后重新运行。\n\n"
            "硅基流动免费 Key 申请：https://cloud.siliconflow.cn/\n"
            "（在「API 密钥」里新建，复制 sk- 开头的字符串）",
            "语音输入 - 请先配置",
            0x40,  # MB_ICONINFORMATION
        )
        os.startfile(str(_cfg_path))  # noqa: S606 -- open in default editor
    except Exception:
        pass
    sys.exit(0)

with open(_cfg_path, encoding="utf-8") as _f:
    _cfg = json.load(_f)

HOTKEY: str = _cfg.get("hotkey", "ctrl+alt+v")
TOGGLE_HOTKEY: str = _cfg.get("toggle_hotkey", "ctrl+shift+r")
PASTE_AFTER: bool = _cfg.get("paste_after", True)
USE_SYSTEM_PROXY: bool = _cfg.get("use_system_proxy", False)
LOG_PATH = _app_dir() / "voice_input.runtime.log"

_active_provider = (_cfg.get("provider") or "siliconflow").lower()
if not _cfg.get(f"{_active_provider}_api_key"):
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"当前 provider 是 {_active_provider}，但它的 api_key 还没填。\n"
            f"请打开 config.json 填入 Key，否则识别会报错。",
            "语音输入 - 缺少 API Key",
            0x30,  # MB_ICONWARNING
        )
    except Exception:
        pass

_MUTEX_HANDLE = None


def _log(message: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} {message}\n")
    except Exception:
        pass


def _ensure_single_instance() -> bool:
    """Return False when another voice-input instance is already running."""
    if os.name != "nt":
        return True

    global _MUTEX_HANDLE
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Global\\CedarVoiceInput")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _get_foreground_window():
    if os.name != "nt":
        return None

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    return user32.GetForegroundWindow()


def _set_foreground_window(hwnd) -> bool:
    if os.name != "nt" or not hwnd:
        return False

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.ShowWindow(hwnd, 5)  # SW_SHOW
    return bool(user32.SetForegroundWindow(hwnd))


def _set_window_no_activate(hwnd):
    if os.name != "nt" or not hwnd:
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        get_window_long = user32.GetWindowLongPtrW
        set_window_long = user32.SetWindowLongPtrW
    else:
        get_window_long = user32.GetWindowLongW
        set_window_long = user32.SetWindowLongW

    get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
    get_window_long.restype = ctypes.c_void_p
    set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
    set_window_long.restype = ctypes.c_void_p

    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    style = int(get_window_long(hwnd, GWL_EXSTYLE) or 0)
    set_window_long(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)

# ---- Indicator (console) ----------------------------------------------------

_LABELS = {
    "idle": "就绪",
    "recording": "录音中...",
    "toggle_recording": "录音中... 再按一次停止",
    "recognizing": "识别中...",
    "done": "已粘贴",
    "error": "出错",
}


class Indicator:
    def __init__(self):
        self._listeners = []

    def add_listener(self, listener):
        self._listeners.append(listener)

    def set_state(self, state: str, extra: str = ""):
        label = _LABELS.get(state, state)
        msg = f"[{label}]" if not extra else f"[{label}] {extra}"
        print(msg, flush=True)
        for listener in self._listeners:
            listener(state)

    def run(self):
        pass

    def destroy(self):
        pass


# ---- Tray icon --------------------------------------------------------------

def _make_tray_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill="#c0392b")
    d.ellipse((20, 14, 44, 42), fill="white")
    d.rectangle((28, 40, 36, 52), fill="white")
    d.rectangle((22, 52, 42, 56), fill="white")
    return img


class TrayIcon(pystray.Icon):
    def __call__(self):
        on_activate = getattr(self, "on_activate", None)
        if on_activate is not None:
            return on_activate()
        return super().__call__()


# ---- Floating button --------------------------------------------------------

class FloatingRecordButton(QtWidgets.QWidget):
    state_changed = QtCore.pyqtSignal(str)

    def __init__(self, toggle_callback, focus_callback=None):
        super().__init__()
        self._toggle_callback = toggle_callback
        self._focus_callback = focus_callback
        self._color = QtGui.QColor("#808080")
        self._press_pos = None
        self._drag_offset = None
        self._dragging = False

        flags = (
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setFixedSize(96, 96)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"点击切换录音状态 ({TOGGLE_HOTKEY})")

        self.setToolTip(f"点击切换录音状态 ({TOGGLE_HOTKEY})")
        self.state_changed.connect(self._apply_state)

    def place_on_screen(self):
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            self.move(80, 160)
            return
        area = screen.availableGeometry()
        x = area.left() + (area.width() - self.width()) // 2
        y = area.bottom() - self.height() - 24
        self.move(x, y)

    def set_state(self, state: str):
        self.state_changed.emit(state)

    def _apply_state(self, state: str):
        self._color = QtGui.QColor("#d93025" if state in {"recording", "toggle_recording"} else "#808080")
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.scale(self.width() / 54, self.height() / 54)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(3, 3, 48, 48)

        painter.setPen(QtGui.QPen(QtGui.QColor("white"), 3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        painter.drawLine(27, 18, 27, 32)
        painter.drawArc(19, 22, 16, 16, 200 * 16, 140 * 16)
        painter.drawLine(27, 38, 27, 43)
        painter.drawLine(21, 43, 33, 43)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return
        if self._focus_callback is not None:
            self._focus_callback()
        self._press_pos = event.globalPos()
        self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & QtCore.Qt.LeftButton) or self._drag_offset is None:
            return
        if self._press_pos is not None and (event.globalPos() - self._press_pos).manhattanLength() > 4:
            self._dragging = True
        self.move(event.globalPos() - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return
        if not self._dragging:
            _log("floating button clicked")
            threading.Thread(target=self._toggle_callback, daemon=True).start()
        self._press_pos = None
        self._drag_offset = None
        self._dragging = False
        event.accept()


# ---- Hotkey parsing ---------------------------------------------------------

def _parse_hotkey(hotkey_str: str) -> tuple[list[str], str]:
    """Split 'ctrl+alt+v' into (['ctrl', 'alt'], 'v')."""
    parts = [p.strip().lower() for p in hotkey_str.split("+")]
    if not parts or not parts[-1]:
        raise ValueError(f"Invalid hotkey: {hotkey_str!r}")
    return parts[:-1], parts[-1]


# ---- Main app ---------------------------------------------------------------

class VoiceInput:
    def __init__(self):
        self._recorder = Recorder()
        self._transcriber = make_transcriber(_cfg)
        self._indicator = Indicator()
        self._lock = threading.Lock()
        self._recording_mode: str | None = None
        self._toggle_trigger_down = False
        self._floating_button = None
        self._tray = None
        self._paste_hwnd = None

    def _remember_paste_target(self):
        hwnd = _get_foreground_window()
        if hwnd:
            if self._floating_button is not None and hwnd == int(self._floating_button.winId()):
                _log("paste target ignored: floating window is foreground")
                return
            self._paste_hwnd = hwnd
            _log(f"paste target remembered hwnd={hwnd}")

    def _restore_paste_target(self):
        if _set_foreground_window(self._paste_hwnd):
            _log(f"paste target restored hwnd={self._paste_hwnd}")
            time.sleep(0.1)
        else:
            _log(f"paste target restore failed hwnd={self._paste_hwnd}")

    def _is_recording(self) -> bool:
        with self._lock:
            return self._recording_mode is not None

    def _set_state_if_not_recording(self, state: str, extra: str = ""):
        if self._is_recording():
            _log(f"state skipped while recording: {state}")
            return
        self._indicator.set_state(state, extra)

    # ---- Recording lifecycle ------------------------------------------------

    def _start_recording(self, mode: str, state: str) -> bool:
        with self._lock:
            if self._recording_mode is not None:
                _log(f"start ignored: already recording mode={self._recording_mode}")
                return False
            self._recording_mode = mode

        self._indicator.set_state(state)
        try:
            self._recorder.start()
            _log(f"recording started mode={mode}")
            return True
        except Exception as exc:
            _log(f"recording start failed: {exc!r}")
            self._indicator.set_state("error", str(exc)[:40])
            with self._lock:
                self._recording_mode = None
            return False

    def _stop_recording(self, expected_mode: str | None = None) -> bool:
        with self._lock:
            if self._recording_mode is None:
                _log("stop ignored: not recording")
                return False
            if expected_mode is not None and self._recording_mode != expected_mode:
                _log(f"stop ignored: expected={expected_mode} actual={self._recording_mode}")
                return False
            self._recording_mode = None

        self._indicator.set_state("recognizing")
        wav = self._recorder.stop()
        _log(f"recording stopped bytes={len(wav)}")
        threading.Thread(target=self._transcribe_and_paste, args=(wav,), daemon=True).start()
        return True

    # ---- Hotkey callbacks (keyboard listener thread) -----------------------

    def _on_hold_press(self):
        self._remember_paste_target()
        self._start_recording("hold", "recording")

    def _on_hold_release(self):
        self._stop_recording("hold")

    def _on_toggle_press(self):
        with self._lock:
            is_toggle_recording = self._recording_mode == "toggle"
            is_busy = self._recording_mode is not None

        if is_toggle_recording:
            self._stop_recording("toggle")
        elif not is_busy:
            self._remember_paste_target()
            self._start_recording("toggle", "toggle_recording")

    def _transcribe_and_paste(self, wav: bytes):
        self._transcribing = True
        try:
            text = self._transcriber.transcribe(wav)
            _log(f"transcribed chars={len(text)}")
            pyperclip.copy(text)
            self._set_state_if_not_recording("done")
            if PASTE_AFTER:
                self._restore_paste_target()
                keyboard.send("ctrl+v")
                _log("pasted text")
            threading.Timer(1.5, lambda: self._set_state_if_not_recording("idle")).start()
        except Exception as exc:
            _log(f"transcribe/paste failed: {exc!r}")
            self._set_state_if_not_recording("error", str(exc)[:40])
            threading.Timer(3.0, lambda: self._set_state_if_not_recording("idle")).start()

    # ---- Wiring -------------------------------------------------------------

    def _register_hotkey(self):
        keyboard.unhook_all()
        hold_modifiers, hold_trigger = _parse_hotkey(HOTKEY)
        toggle_modifiers, toggle_trigger = _parse_hotkey(TOGGLE_HOTKEY)

        def _check_mods(modifiers: list[str]) -> bool:
            return all(keyboard.is_pressed(m) for m in modifiers)

        def _on_event(event):
            if event.name.lower() == hold_trigger:
                if event.event_type == keyboard.KEY_DOWN and _check_mods(hold_modifiers):
                    self._on_hold_press()
                elif event.event_type == keyboard.KEY_UP:
                    self._on_hold_release()

            if event.name.lower() == toggle_trigger:
                if event.event_type == keyboard.KEY_DOWN and _check_mods(toggle_modifiers):
                    if not self._toggle_trigger_down:
                        self._toggle_trigger_down = True
                        self._on_toggle_press()
                elif event.event_type == keyboard.KEY_UP:
                    self._toggle_trigger_down = False

        keyboard.hook(_on_event)

    def _build_tray(self) -> pystray.Icon:
        def _quit(icon, _item):
            icon.stop()
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.quit()

        menu = pystray.Menu(pystray.MenuItem("Record", lambda i,t: self._on_toggle_press(), default=True), pystray.MenuItem("Quit", _quit))
        icon = TrayIcon("voice-input", _make_tray_image(), "Voice Input", menu)
        icon.on_activate = self._on_toggle_press
        return icon

    def run(self):
        if not _ensure_single_instance():
            print("[voice-input] 已经在运行，退出本次启动。", flush=True)
            return

        self._register_hotkey()
        print(f"[就绪] 按住 {HOTKEY} 录音；按 {TOGGLE_HOTKEY} 开始/停止录音", flush=True)

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        self._floating_button = FloatingRecordButton(
            self._on_toggle_press,
            self._remember_paste_target,
        )
        self._indicator.add_listener(self._floating_button.set_state)
        self._floating_button.place_on_screen()
        self._floating_button.show()
        _set_window_no_activate(int(self._floating_button.winId()))
        self._floating_button.raise_()

        self._tray = self._build_tray()
        self._tray.run_detached()
        app.aboutToQuit.connect(self._tray.stop)
        app.exec_()


if __name__ == "__main__":
    VoiceInput().run()
