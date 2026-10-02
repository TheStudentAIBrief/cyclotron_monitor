"""H3 hardening: model artifact integrity (keyed HMAC, backward-compatible).

With MODEL_HMAC_KEY set the sidecar is a keyed HMAC an attacker who can only write the
model directory cannot forge. A model with only the legacy unkeyed SHA-256 sidecar (which
anyone who can write the file can also recompute) is refused unless MODEL_ALLOW_UNSIGNED=1.
An unsigned model cannot be made trustworthy afterwards -- signing what is on disk would bless
a swapped file too -- so it has to be re-trained with the key set.
"""
import pickle

import pytest

from models import predictor, trainer
from models.trainer import _write_checksum
from models.predictor import _verify_checksum


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ('MODEL_HMAC_KEY', 'MODEL_REQUIRE_SIGNED', 'MODEL_ALLOW_UNSIGNED'):
        monkeypatch.delenv(name, raising=False)
    predictor._model_cache.clear()


def _make_pkl(tmp_path):
    p = tmp_path / 'm.pkl'
    p.write_bytes(pickle.dumps({'x': 1}))
    return p


def test_an_unsigned_model_is_refused_unless_explicitly_allowed(tmp_path, monkeypatch):
    p = _make_pkl(tmp_path)
    _write_checksum(p)                                    # no key: legacy unkeyed checksum

    with pytest.raises(predictor.ModelIntegrityError, match='re-train'):
        _verify_checksum(p)

    monkeypatch.setenv('MODEL_ALLOW_UNSIGNED', '1')
    assert _verify_checksum(p) == p.read_bytes()


def test_a_forged_unsigned_model_is_refused_by_default(tmp_path):
    # The attack: overwrite the pkl AND recompute a matching bare-hex sidecar.
    p = _make_pkl(tmp_path)
    p.write_bytes(b'EVIL-PAYLOAD')
    _write_checksum(p)

    with pytest.raises(predictor.ModelIntegrityError):
        _verify_checksum(p)


def test_a_missing_or_changed_model_is_an_integrity_error(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_HMAC_KEY', 'topsecret-key')
    p = _make_pkl(tmp_path)
    with pytest.raises(predictor.ModelIntegrityError):    # no sidecar at all
        _verify_checksum(p)
    _write_checksum(p)
    p.write_bytes(b'EVIL-PAYLOAD')
    with pytest.raises(predictor.ModelIntegrityError):
        _verify_checksum(p)


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
    monkeypatch.setenv('MODEL_ALLOW_UNSIGNED', '1')
    monkeypatch.setenv('MODEL_REQUIRE_SIGNED', '1')
    with pytest.raises(RuntimeError):     # strict mode wins even when unsigned models are allowed
        _verify_checksum(p)


# ── a model that cannot be trusted is not run, but monitoring carries on ─────

class _Boom:
    def __reduce__(self):
        return (pytest.fail, ('an unverified model file was unpickled',))


def test_predicting_with_a_model_that_cannot_be_verified_falls_back_to_the_counter(tmp_path):
    model = tmp_path / 'ion_source_model.pkl'
    model.write_bytes(pickle.dumps(_Boom()))
    _write_checksum(model)                                # unsigned: refused by default
    (tmp_path / 'ion_source_days_calibrator.pkl').write_bytes(pickle.dumps(_Boom()))

    result = predictor.predict('ION SOURCE', {}, str(tmp_path), counter_days=5.0, last_maintenance='2026-08-01')

    assert result.primary_signal == 'COUNTER_ONLY'
    assert result.alert_level == 'ORANGE' and result.days_estimate == 5.0
    assert result.warning == predictor.UNVERIFIED_MODEL_WARNING


def _unsigned_model(tmp_path):
    for name in ('ion_source_model.pkl', 'ion_source_days_calibrator.pkl'):
        (tmp_path / name).write_bytes(pickle.dumps(_Boom()))
        _write_checksum(tmp_path / name)


def test_a_component_whose_model_was_refused_is_never_shown_as_green(tmp_path):
    """The counter cannot see what the model would have seen. Showing GREEN would
    hide an alarm the model might have raised, so the level is held at YELLOW."""
    _unsigned_model(tmp_path)

    result = predictor.predict('ION SOURCE', {}, str(tmp_path), counter_days=60.0, last_maintenance='2026-08-01')

    assert (result.alert_level, result.days_estimate, result.primary_signal) == ('YELLOW', 60.0, 'COUNTER_ONLY')
    assert 'may be too low' in result.warning


def test_a_component_with_no_model_at_all_is_still_green_on_a_healthy_counter(tmp_path):
    result = predictor.predict('ION SOURCE', {}, str(tmp_path), counter_days=60.0, last_maintenance='2026-08-01')

    assert result.alert_level == 'GREEN' and result.warning is None


def test_a_missing_calibrator_file_falls_back_instead_of_failing(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_HMAC_KEY', 'topsecret-key')
    _unsigned_model(tmp_path)                             # signed, since a key is set
    (tmp_path / 'ion_source_days_calibrator.pkl').unlink()
    (tmp_path / 'ion_source_model.pkl').write_bytes(pickle.dumps({'model': None, 'feature_names': []}))
    _write_checksum(tmp_path / 'ion_source_model.pkl')

    result = predictor.predict('ION SOURCE', {}, str(tmp_path), counter_days=60.0, last_maintenance='2026-08-01')

    assert result.primary_signal == 'COUNTER_ONLY' and result.alert_level == 'YELLOW'


def test_a_signed_model_swapped_for_another_file_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_HMAC_KEY', 'topsecret-key')
    _unsigned_model(tmp_path)                             # signed under the key
    (tmp_path / 'ion_source_model.pkl').write_bytes(pickle.dumps(_Boom()) + b' ')

    result = predictor.predict('ION SOURCE', {}, str(tmp_path), counter_days=60.0, last_maintenance='2026-08-01')

    assert result.primary_signal == 'COUNTER_ONLY'


def test_there_is_no_way_to_sign_a_model_that_is_already_on_disk():
    """It would sign a swapped file whose plain checksum had been recomputed."""
    import main
    assert not hasattr(trainer, 'sign_existing_models')
    assert 'sign-models' not in main.COMMANDS


# ── third pass: findings of the second review ────────────────────────────────

@pytest.mark.parametrize('left_behind', [
    'ion_source_days_calibrator.pkl', 'ion_source_model.sha256', 'ion_source_days_calibrator.sha256'])
def test_a_deleted_model_file_is_not_mistaken_for_a_component_that_never_had_a_model(tmp_path, left_behind):
    """Deleting the model instead of swapping it must not buy a quiet GREEN."""
    (tmp_path / left_behind).write_bytes(b'x')

    result = predictor.predict('ION SOURCE', {}, str(tmp_path), counter_days=60.0, last_maintenance='2026-08-01')

    assert result.alert_level == 'YELLOW' and result.warning == predictor.UNVERIFIED_MODEL_WARNING


def test_an_integrity_file_that_is_not_text_falls_back_instead_of_failing(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_HMAC_KEY', 'topsecret-key')
    _unsigned_model(tmp_path)
    (tmp_path / 'ion_source_model.sha256').write_bytes(b'\x81\x8d\xff\x00 not text')

    result = predictor.predict('ION SOURCE', {}, str(tmp_path), counter_days=60.0, last_maintenance='2026-08-01')

    assert result.primary_signal == 'COUNTER_ONLY' and result.alert_level == 'YELLOW'


def test_a_model_that_could_not_be_verified_is_named_in_the_alert_file(tmp_path):
    """The dashboard's YELLOW is not enough: the alert file is what gets acted on."""
    from monitor.dashboard_writer import write_dashboard
    _unsigned_model(tmp_path)
    unverified = predictor.predict('ION SOURCE', {}, str(tmp_path), counter_days=60.0, last_maintenance='x')
    healthy = predictor.predict('FOILS', {}, str(tmp_path), counter_days=60.0, last_maintenance='x')
    alert = tmp_path / 'alert.txt'

    write_dashboard([unverified, healthy], str(tmp_path / 'dashboard.json'), str(alert))

    text = alert.read_text()
    assert 'ION SOURCE: MODEL NOT VERIFIED' in text and 'FOILS' not in text


def test_a_healthy_set_of_components_still_writes_no_alert_file(tmp_path):
    from monitor.dashboard_writer import write_dashboard
    healthy = predictor.predict('FOILS', {}, str(tmp_path), counter_days=60.0, last_maintenance='x')

    write_dashboard([healthy], str(tmp_path / 'dashboard.json'), str(tmp_path / 'alert.txt'))

    assert not (tmp_path / 'alert.txt').exists()
