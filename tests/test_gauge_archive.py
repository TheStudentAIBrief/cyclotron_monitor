"""
TDD tests for monitor/gauge_archive.py

The gauge archive is a write-once audit trail. Each imported EUR form gets a
timestamped directory containing the original photo, raw OCR response, and
parsed gauge readings. A master manifest.json is appended on every import.
Nothing in the archive is ever modified or deleted.
"""
import json
import os
import pytest
from monitor.gauge_archive import archive_import, MANIFEST_NAME


# ── Directory and file creation ────────────────────────────────────────────────

def test_archive_creates_directory(tmp_path):
    archive_dir = str(tmp_path / 'gauge_archive')
    archive_import('image0.jpg', b'FAKEJPEG', '{"entries":[]}', [], archive_dir)
    assert os.path.isdir(archive_dir)


def test_archive_creates_entry_subdirectory(tmp_path):
    archive_dir = str(tmp_path / 'gauge_archive')
    archive_import('image0.jpg', b'FAKEJPEG', '{}', [], archive_dir)
    subdirs = [
        f for f in os.listdir(archive_dir)
        if os.path.isdir(os.path.join(archive_dir, f))
    ]
    assert len(subdirs) == 1


def test_archive_saves_original_photo(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    archive_import('image0.jpg', b'\xff\xd8\xff', '{}', [], archive_dir)
    subdirs = [f for f in os.listdir(archive_dir) if os.path.isdir(os.path.join(archive_dir, f))]
    photo = os.path.join(archive_dir, subdirs[0], 'original.jpg')
    assert os.path.exists(photo)
    assert open(photo, 'rb').read() == b'\xff\xd8\xff'


def test_archive_saves_ocr_response_as_valid_json(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    ocr = '{"entries": [{"date": "04/02/2023"}]}'
    archive_import('x.jpg', b'', ocr, [], archive_dir)
    subdirs = [f for f in os.listdir(archive_dir) if os.path.isdir(os.path.join(archive_dir, f))]
    path = os.path.join(archive_dir, subdirs[0], 'ocr_response.json')
    assert os.path.exists(path)
    saved = json.loads(open(path).read())
    assert saved['entries'][0]['date'] == '04/02/2023'


def test_archive_handles_invalid_ocr_json(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    archive_import('x.jpg', b'', 'not json', [], archive_dir)
    subdirs = [f for f in os.listdir(archive_dir) if os.path.isdir(os.path.join(archive_dir, f))]
    path = os.path.join(archive_dir, subdirs[0], 'ocr_response.json')
    assert os.path.exists(path)  # falls back gracefully


def test_archive_saves_parsed_readings(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    readings = [{'gauge_name': 'Gas Flow', 'value': 6.2, 'unit': 'Sccm'}]
    archive_import('x.jpg', b'', '{}', readings, archive_dir)
    subdirs = [f for f in os.listdir(archive_dir) if os.path.isdir(os.path.join(archive_dir, f))]
    path = os.path.join(archive_dir, subdirs[0], 'parsed_readings.json')
    saved = json.loads(open(path).read())
    assert saved == readings


# ── Manifest ───────────────────────────────────────────────────────────────────

def test_manifest_file_created(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    archive_import('x.jpg', b'', '{}', [], archive_dir)
    assert os.path.exists(os.path.join(archive_dir, MANIFEST_NAME))


def test_manifest_contains_source_file(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    archive_import('image0.jpg', b'', '{}', [], archive_dir)
    manifest = json.loads(open(os.path.join(archive_dir, MANIFEST_NAME)).read())
    assert manifest[0]['source_file'] == 'image0.jpg'


def test_manifest_contains_readings_count(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    readings = [{'gauge_name': 'Vacuum'}] * 3
    archive_import('x.jpg', b'', '{}', readings, archive_dir)
    manifest = json.loads(open(os.path.join(archive_dir, MANIFEST_NAME)).read())
    assert manifest[0]['readings_count'] == 3


def test_manifest_appends_on_second_call(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    archive_import('image0.jpg', b'', '{}', [], archive_dir)
    archive_import('image1.jpg', b'', '{}', [], archive_dir)
    manifest = json.loads(open(os.path.join(archive_dir, MANIFEST_NAME)).read())
    assert len(manifest) == 2
    assert {m['source_file'] for m in manifest} == {'image0.jpg', 'image1.jpg'}


def test_manifest_contains_archived_at_timestamp(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    archive_import('x.jpg', b'', '{}', [], archive_dir)
    manifest = json.loads(open(os.path.join(archive_dir, MANIFEST_NAME)).read())
    assert 'archived_at' in manifest[0]
    assert len(manifest[0]['archived_at']) == 16  # YYYYMMDDTHHMMSSz format


# ── Append-only / immutability ─────────────────────────────────────────────────

def test_archiving_same_file_twice_keeps_both(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    archive_import('image0.jpg', b'V1', '{}', [], archive_dir)
    archive_import('image0.jpg', b'V2', '{}', [], archive_dir)
    manifest = json.loads(open(os.path.join(archive_dir, MANIFEST_NAME)).read())
    assert len(manifest) == 2


def test_second_call_does_not_overwrite_first_photo(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    archive_import('image0.jpg', b'VERSION1', '{}', [], archive_dir)
    archive_import('image0.jpg', b'VERSION2', '{}', [], archive_dir)
    # Both entry dirs exist
    subdirs = sorted([
        f for f in os.listdir(archive_dir)
        if os.path.isdir(os.path.join(archive_dir, f))
    ])
    assert len(subdirs) == 2
    p1 = open(os.path.join(archive_dir, subdirs[0], 'original.jpg'), 'rb').read()
    p2 = open(os.path.join(archive_dir, subdirs[1], 'original.jpg'), 'rb').read()
    assert p1 == b'VERSION1'
    assert p2 == b'VERSION2'


# ── Return value ───────────────────────────────────────────────────────────────

def test_returns_path_to_entry_directory(tmp_path):
    archive_dir = str(tmp_path / 'ga')
    entry_dir = archive_import('x.jpg', b'', '{}', [], archive_dir)
    assert os.path.isdir(entry_dir)
    assert os.path.exists(os.path.join(entry_dir, 'original.jpg'))
