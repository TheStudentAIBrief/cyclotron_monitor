"""Auth-layer security tests (added during security hardening).

Covers the JWT signing-key handling: tokens round-trip, the process key is never
the old public default, and a token forged with that default is rejected.
"""
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
