"""H3 hardening: model artifact integrity (keyed HMAC, backward-compatible).

Legacy unkeyed SHA-256 sidecars still verify (no breakage for already-trained models),
but with MODEL_HMAC_KEY set the sidecar is a keyed HMAC an attacker who can only write the
model directory cannot forge, and MODEL_REQUIRE_SIGNED=1 refuses unsigned models entirely.
"""
import pickle

import pytest

from models.trainer import _write_checksum
from models.predictor import _verify_checksum


def _make_pkl(tmp_path):
    p = tmp_path / 'm.pkl'
    p.write_bytes(pickle.dumps({'x': 1}))
    return p


def test_legacy_unkeyed_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv('MODEL_HMAC_KEY', raising=False)
    monkeypatch.delenv('MODEL_REQUIRE_SIGNED', raising=False)
    p = _make_pkl(tmp_path)
    _write_checksum(p)
    assert _verify_checksum(p) == p.read_bytes()          # unchanged behavior preserved


def test_keyed_signing_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_HMAC_KEY', 'topsecret-key')
    p = _make_pkl(tmp_path)
    _write_checksum(p)
    assert p.with_suffix('.sha256').read_text().startswith('hmac-sha256:')
    assert _verify_checksum(p) == p.read_bytes()


def test_keyed_model_rejected_with_wrong_key(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_HMAC_KEY', 'topsecret-key')
    p = _make_pkl(tmp_path)
    _write_checksum(p)
    monkeypatch.setenv('MODEL_HMAC_KEY', 'attacker-guess')
    with pytest.raises(RuntimeError):
        _verify_checksum(p)


def test_tampered_keyed_model_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_HMAC_KEY', 'topsecret-key')
    p = _make_pkl(tmp_path)
    _write_checksum(p)
    p.write_bytes(b'EVIL-PAYLOAD')        # attacker can't recompute the HMAC without the key
    with pytest.raises(RuntimeError):
        _verify_checksum(p)


def test_forged_legacy_sidecar_rejected_in_strict_mode(tmp_path, monkeypatch):
    # The H3 attack: attacker overwrites the pkl AND recomputes a matching bare-hex sidecar.
    monkeypatch.delenv('MODEL_HMAC_KEY', raising=False)
    p = _make_pkl(tmp_path)
    _write_checksum(p)
    p.write_bytes(b'EVIL-PAYLOAD')
    _write_checksum(p)                    # attacker re-signs the unkeyed sidecar
    monkeypatch.setenv('MODEL_REQUIRE_SIGNED', '1')
    with pytest.raises(RuntimeError):     # strict mode refuses any unsigned model
        _verify_checksum(p)
