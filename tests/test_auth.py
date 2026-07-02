"""Auth-layer security tests (added during security hardening).

Covers the JWT signing-key handling: tokens round-trip, the process key is never
the old public default, and a token forged with that default is rejected.
"""
import json

import jwt
import pytest
from fastapi import HTTPException

from api import auth


def test_access_token_roundtrips():
    toks = auth.create_tokens('alice', 'lab1')
    payload = auth._decode(toks['access_token'])
    assert payload['sub'] == 'alice'
    assert payload['lab_id'] == 'lab1'
    assert payload['type'] == 'access'


def test_refresh_token_type_is_enforced():
    toks = auth.create_tokens('alice', 'lab1')
    # an access token must not be usable where a refresh token is required
    access_payload = auth._decode(toks['access_token'])
    assert access_payload['type'] == 'access'
    refresh_payload = auth._decode(toks['refresh_token'])
    assert refresh_payload['type'] == 'refresh'


def test_signing_key_is_not_the_old_public_default():
    assert auth._SECRET != 'dev-secret-change-in-production'


def test_token_forged_with_old_default_secret_is_rejected():
    forged = jwt.encode(
        {'sub': 'attacker', 'lab_id': 'petlabs-pretoria', 'type': 'access'},
        'dev-secret-change-in-production', algorithm='HS256',
    )
    with pytest.raises(HTTPException):
        auth._decode(forged)


# ── Bootstrap credentials (fresh deploy, no data/.credentials.json yet) ────────

def test_bootstrap_creates_credentials_when_missing(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'petlab.db')
    monkeypatch.setenv('BOOTSTRAP_USERNAME', 'admin')
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', 'a-strong-password-123')
    auth.ensure_bootstrap_credentials(db_path)
    creds_path = tmp_path / '.credentials.json'
    assert creds_path.exists()
    creds = json.loads(creds_path.read_text())
    assert creds['username'] == 'admin'
    assert auth._verify_password('a-strong-password-123', creds['hash'])


def test_bootstrap_does_not_overwrite_existing_credentials(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'petlab.db')
    creds_path = tmp_path / '.credentials.json'
    creds_path.write_text(json.dumps({'username': 'existing', 'hash': 'unchanged'}))
    monkeypatch.setenv('BOOTSTRAP_USERNAME', 'admin')
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', 'a-strong-password-123')
    auth.ensure_bootstrap_credentials(db_path)
    assert json.loads(creds_path.read_text()) == {'username': 'existing', 'hash': 'unchanged'}


def test_bootstrap_does_nothing_when_env_vars_unset(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'petlab.db')
    monkeypatch.delenv('BOOTSTRAP_USERNAME', raising=False)
    monkeypatch.delenv('BOOTSTRAP_PASSWORD', raising=False)
    auth.ensure_bootstrap_credentials(db_path)
    assert not (tmp_path / '.credentials.json').exists()


def test_bootstrap_logs_when_env_vars_unset(tmp_path, monkeypatch, caplog):
    # Regression: this branch used to return silently with zero log output,
    # indistinguishable in Render's logs from a healthy no-op (credentials
    # already existing) - impossible to diagnose "why can't I log in" remotely.
    db_path = str(tmp_path / 'petlab.db')
    monkeypatch.delenv('BOOTSTRAP_USERNAME', raising=False)
    monkeypatch.delenv('BOOTSTRAP_PASSWORD', raising=False)
    with caplog.at_level('INFO', logger='uvicorn.error'):
        auth.ensure_bootstrap_credentials(db_path)
    assert 'BOOTSTRAP_USERNAME' in caplog.text
    assert 'BOOTSTRAP_PASSWORD' in caplog.text


def test_bootstrap_rejects_short_password(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'petlab.db')
    monkeypatch.setenv('BOOTSTRAP_USERNAME', 'admin')
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', 'short')
    auth.ensure_bootstrap_credentials(db_path)
    assert not (tmp_path / '.credentials.json').exists()


def test_bootstrap_logs_when_credentials_already_exist(tmp_path, monkeypatch, caplog):
    db_path = str(tmp_path / 'petlab.db')
    creds_path = tmp_path / '.credentials.json'
    creds_path.write_text(json.dumps({'username': 'existing', 'hash': 'unchanged'}))
    monkeypatch.setenv('BOOTSTRAP_USERNAME', 'admin')
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', 'a-strong-password-123')
    with caplog.at_level('INFO', logger='uvicorn.error'):
        auth.ensure_bootstrap_credentials(db_path)
    assert 'already exist' in caplog.text.lower()


def test_bootstrapped_credentials_authenticate_successfully(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'petlab.db')
    monkeypatch.setenv('BOOTSTRAP_USERNAME', 'admin')
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', 'a-strong-password-123')
    auth.ensure_bootstrap_credentials(db_path)

    from api import config as _config
    monkeypatch.setenv('DATABASE_PATH', db_path)
    _config.get_config.cache_clear()
    try:
        assert auth.authenticate('admin', 'a-strong-password-123') is True
        assert auth.authenticate('admin', 'wrong-password') is False
    finally:
        _config.get_config.cache_clear()
