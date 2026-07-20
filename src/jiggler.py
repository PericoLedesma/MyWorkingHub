"""Mouse jiggler — moves the cursor slightly at intervals, stops on user input."""
import random
import threading
import time
from typing import Callable

import pyautogui

pyautogui.FAILSAFE = False


class MouseJiggler:
    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._kb_listener = None
        self._on_auto_stop: Callable | None = None
        self._last_jiggle_pos: tuple[int, int] | None = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self, interval: int = 30, on_auto_stop: Callable | None = None) -> None:
        if self._running:
            return
        self._running = True
        self._on_auto_stop = on_auto_stop
        self._last_jiggle_pos = None
        self._thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self._thread.start()
        self._start_kb_listener()

    def stop(self) -> None:
        self._running = False
        self._stop_kb_listener()

    def _start_kb_listener(self) -> None:
        try:
            from pynput import keyboard as kb_module
            self._kb_listener = kb_module.Listener(on_press=self._on_key)
            self._kb_listener.start()
        except Exception:
            pass

    def _stop_kb_listener(self) -> None:
        if self._kb_listener:
            try:
                self._kb_listener.stop()
            except Exception:
                pass
            self._kb_listener = None

    def _on_key(self, key) -> None:
        if self._running:
            self.stop()
            if self._on_auto_stop:
                self._on_auto_stop()

    def _run(self, interval: int) -> None:
        while self._running:
            time.sleep(interval)
            if not self._running:
                break

            # If user moved mouse since last jiggle, stop
            if self._last_jiggle_pos:
                cx, cy = pyautogui.position()
                lx, ly = self._last_jiggle_pos
                if abs(cx - lx) > 5 or abs(cy - ly) > 5:
                    self.stop()
                    if self._on_auto_stop:
                        self._on_auto_stop()
                    return

            # Nudge and return — no net displacement
            dx = random.choice([-1, 1]) * random.randint(80, 150)
            dy = random.choice([-1, 1]) * random.randint(80, 150)
            pyautogui.moveRel(dx, dy, duration=0.12)
            pyautogui.moveRel(-dx, -dy, duration=0.12)
            self._last_jiggle_pos = pyautogui.position()
