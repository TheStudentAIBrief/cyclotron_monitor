"""
On-demand Ollama lifecycle manager.

Starts `ollama serve` the first time an AI route needs it, and kills it
after IDLE_SECONDS of no AI activity. Combined with OLLAMA_KEEP_ALIVE=0
this means RAM is only consumed during actual inference.
"""
import subprocess
import time
from threading import Lock, Timer

import httpx

OLLAMA_HOST = "http://localhost:11434"
IDLE_SECONDS = 300  # shut down after 5 min of no AI requests

_lock = Lock()
_idle_timer: Timer | None = None

_CREATE_NO_WINDOW = 0x08000000  # Windows: no console popup


def _is_running() -> bool:
    try:
        httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=2.0).raise_for_status()
        return True
    except Exception:
        return False


def _start() -> None:
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )
    for _ in range(30):  # wait up to 15 s
        time.sleep(0.5)
        if _is_running():
            return
    raise RuntimeError("ollama serve did not start within 15 s")


def _stop() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "ollama.exe"],
        capture_output=True,
    )


def _reset_idle_timer() -> None:
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
    _idle_timer = Timer(IDLE_SECONDS, _stop)
    _idle_timer.daemon = True
    _idle_timer.start()


def ensure_running() -> None:
    """Call before any Ollama inference. Starts the server if needed and resets the idle timer."""
    with _lock:
        if not _is_running():
            _start()
        _reset_idle_timer()
