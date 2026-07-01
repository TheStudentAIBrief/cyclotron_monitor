"""
On-demand Ollama lifecycle manager.

Starts `ollama serve` the first time an AI route needs it, and kills it
after IDLE_SECONDS of no AI activity. Combined with OLLAMA_KEEP_ALIVE=0
this means RAM is only consumed during actual inference.

OS-aware: the Windows console-suppression flag and taskkill are only used on
Windows; POSIX (e.g. the Render Linux container) uses plain spawn + pkill.
A missing `ollama` binary or a non-local OLLAMA_HOST raises a clear RuntimeError
instead of a cryptic platform ValueError.
"""
import os
import subprocess
import time
from threading import Lock, Timer

import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
IDLE_SECONDS = 300  # shut down after 5 min of no AI requests

_lock = Lock()
_idle_timer: Timer | None = None

_CREATE_NO_WINDOW = 0x08000000  # Windows: no console popup
_IS_WINDOWS = os.name == "nt"
# Only auto-spawn a local server when the host is loopback — a remote/on-prem
# Ollama is someone else's process to manage.
_IS_LOCAL = any(h in OLLAMA_HOST for h in ("localhost", "127.0.0.1"))


def _is_running() -> bool:
    try:
        httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=2.0).raise_for_status()
        return True
    except Exception:
        return False


def _start() -> None:
    # creationflags is Windows-only — passing it (even 0-valued) on POSIX raises ValueError.
    kwargs = {"creationflags": _CREATE_NO_WINDOW} if _IS_WINDOWS else {}
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ollama binary not installed on this host") from e
    for _ in range(30):  # wait up to 15 s
        time.sleep(0.5)
        if _is_running():
            return
    raise RuntimeError("ollama serve did not start within 15 s")


def _stop() -> None:
    if _IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
    else:
        subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True)


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
            if not _IS_LOCAL:
                raise RuntimeError(f"Ollama not reachable at {OLLAMA_HOST}")
            _start()
        _reset_idle_timer()
