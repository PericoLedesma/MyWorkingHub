"""Mouse jiggler — moves the cursor slightly at intervals, stops on user input."""
import ctypes
import random
import threading
import time
from typing import Callable

import pyautogui

pyautogui.FAILSAFE = False

# Windows execution-state flags — prevent display off and system sleep
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def _set_execution_state(prevent: bool) -> None:
    try:
        flags = (_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED) if prevent else _ES_CONTINUOUS
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass

# Pixel threshold: movements smaller than this are treated as the jiggler itself
_JIGGLE_SIZE = 5


class MouseJiggler:
    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._kb_listener = None
        self._mouse_listener = None
        self._on_auto_stop: Callable | None = None
        self._jiggling = False  # True while the jiggler is mid-move

    @property
    def running(self) -> bool:
        return self._running

    def start(self, interval: int = 60, on_auto_stop: Callable | None = None) -> None:
        if self._running:
            return
        self._running = True
        self._on_auto_stop = on_auto_stop
        self._jiggling = False
        _set_execution_state(prevent=True)
        self._thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self._thread.start()
        self._start_input_listeners()

    def stop(self) -> None:
        self._running = False
        _set_execution_state(prevent=False)
        self._stop_input_listeners()

    def _start_input_listeners(self) -> None:
        try:
            from pynput import keyboard as kb_module, mouse as mouse_module
            self._kb_listener = kb_module.Listener(on_press=self._on_user_input)
            self._kb_listener.start()
            self._mouse_listener = mouse_module.Listener(on_move=self._on_mouse_move)
            self._mouse_listener.start()
        except Exception:
            pass

    def _stop_input_listeners(self) -> None:
        for attr in ("_kb_listener", "_mouse_listener"):
            listener = getattr(self, attr)
            if listener:
                try:
                    listener.stop()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _on_user_input(self, key) -> None:
        if self._running:
            self._trigger_auto_stop()

    def _on_mouse_move(self, x, y) -> None:
        # Ignore synthetic moves produced by the jiggler itself
        if self._running and not self._jiggling:
            self._trigger_auto_stop()

    def _trigger_auto_stop(self) -> None:
        if not self._running:
            return
        self.stop()
        if self._on_auto_stop:
            self._on_auto_stop()

    def _run(self, interval: int) -> None:
        while self._running:
            time.sleep(interval)
            if not self._running:
                break

            # Tiny nudge and return — no net displacement, hard to notice
            dx = random.choice([-1, 1]) * random.randint(2, _JIGGLE_SIZE)
            dy = random.choice([-1, 1]) * random.randint(2, _JIGGLE_SIZE)
            self._jiggling = True
            pyautogui.moveRel(dx, dy, duration=0.05)
            pyautogui.moveRel(-dx, -dy, duration=0.05)
            self._jiggling = False
