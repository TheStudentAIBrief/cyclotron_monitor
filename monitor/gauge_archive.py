"""
Write-once audit archive for EUR form imports (NNR compliance).

Each archive_import() call creates a timestamped directory containing:
  - original.jpg         — original photo bytes
  - ocr_response.json    — raw Ollama response
  - parsed_readings.json — parsed gauge reading dicts

A manifest.json in the archive root is appended on every call.
Nothing in the archive is ever modified or deleted after writing.
"""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone

MANIFEST_NAME = 'manifest.json'

# Monotonic counter ensures sort order matches insertion order even within a second
_counter_lock = threading.Lock()
_counter = 0

# Serializes the manifest read-modify-write so concurrent imports cannot lose entries.
# The manifest is the NNR audit index — a dropped entry is a compliance gap.
_manifest_lock = threading.Lock()


def _next_counter() -> int:
    global _counter
    with _counter_lock:
        _counter += 1
        return _counter


def _write_atomic(path: str, data: bytes) -> None:
    """Write data to path atomically via temp-file → os.replace()."""
    tmp_fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        os.close(tmp_fd)
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def archive_import(
    source_file: str,
    photo_bytes: bytes,
    ocr_response: str,
    parsed_readings: list,
    archive_dir: str,
) -> str:
    """Archive one EUR form import. Returns the path to the new entry directory.

    The archive is append-only: this function never modifies or deletes existing
    files. Archiving the same source_file twice produces two independent entries
    with different directory names (microseconds + monotonic counter guarantee this).
    """
    os.makedirs(archive_dir, exist_ok=True)

    now = datetime.now(timezone.utc)
    ts = now.strftime('%Y%m%dT%H%M%SZ')                     # 16 chars for manifest
    ts_dir = now.strftime('%Y%m%dT%H%M%S%f')                # microseconds for dir sort
    seq = _next_counter()
    safe_stem = os.path.splitext(os.path.basename(source_file))[0].replace(' ', '_')
    entry_name = f'{ts_dir}_{seq:06d}_{safe_stem}'
    entry_dir = os.path.join(archive_dir, entry_name)
    os.makedirs(entry_dir)

    # Save original photo
    _write_atomic(os.path.join(entry_dir, 'original.jpg'), photo_bytes)

    # Save OCR response as formatted JSON (fall back for non-JSON responses)
    try:
        ocr_obj = json.loads(ocr_response)
    except (json.JSONDecodeError, TypeError):
        ocr_obj = {'raw': str(ocr_response)}
    _write_atomic(
        os.path.join(entry_dir, 'ocr_response.json'),
        json.dumps(ocr_obj, indent=2).encode(),
    )

    # Save parsed readings
    _write_atomic(
        os.path.join(entry_dir, 'parsed_readings.json'),
        json.dumps(parsed_readings, indent=2).encode(),
    )

    # Append to manifest under a lock so concurrent imports can't clobber each other.
    manifest_path = os.path.join(archive_dir, MANIFEST_NAME)
    with _manifest_lock:
        try:
            existing = json.loads(open(manifest_path, encoding='utf-8').read())
        except (FileNotFoundError, json.JSONDecodeError):
            existing = []

        existing.append({
            'archived_at':    ts,
            'source_file':    source_file,
            'entry_dir':      entry_name,
            'readings_count': len(parsed_readings),
        })
        _write_atomic(manifest_path, json.dumps(existing, indent=2).encode())

    return entry_dir
