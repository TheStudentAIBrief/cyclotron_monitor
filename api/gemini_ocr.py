"""
Gemini Vision API client for gauge OCR and EUR form parsing.

Replaces qwen2.5vl:7b (Ollama) for the two gauge photo endpoints.
The existing prompts and JSON schemas carry over unchanged — they encode
all the PET lab domain knowledge and work better with Gemini than with
a local 7B model.

Setup: get a free API key at https://aistudio.google.com/app/apikey
(no credit card — free tier is 1 500 req/day, 15 req/min).
Set GEMINI_API_KEY *and* ALLOW_CLOUD_OCR=1 in your environment, then restart
the API server.

DATA-EGRESS POLICY: every call() ships the full photo (base64) plus the domain
prompt to Google's US cloud. For an NNR-regulated facility with air-gap /
data-residency expectations that must be a deliberate, documented decision —
not a side effect of an API key being present. Cloud OCR is therefore gated
behind the explicit, default-OFF ALLOW_CLOUD_OCR flag; without it, OCR uses
the on-prem Ollama model only (GAUGE_OLLAMA_MODEL) and nothing leaves the
facility.
"""
import os
import random
import time

import httpx


def _flag(name: str) -> bool:
    """Explicit truthy env flag ('1'/'true'/'yes'/'on') — presence alone is not consent."""
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


GEMINI_API_KEY  = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL    = os.environ.get('GEMINI_OCR_MODEL', 'gemini-2.0-flash')
ALLOW_CLOUD_OCR = _flag('ALLOW_CLOUD_OCR')   # see DATA-EGRESS POLICY in the module docstring
_BASE           = 'https://generativelanguage.googleapis.com/v1beta/models'

_MAX_ATTEMPTS      = 4
_BASE_DELAY        = 2
_BACKOFF_FACTOR    = 2
_MAX_RETRY_AFTER   = 30
_JITTER_MAX        = 0.5

_sleep = time.sleep


def is_configured() -> bool:
    # Both are required: the key alone must never be enough to start sending
    # facility photos to a third-party US cloud (data-egress opt-in, default off).
    return ALLOW_CLOUD_OCR and bool(GEMINI_API_KEY)


def _mime(b64: str) -> str:
    """Infer image MIME type from base64 magic bytes."""
    if b64.startswith('/9j/'):
        return 'image/jpeg'
    if b64.startswith('iVBOR'):
        return 'image/png'
    return 'image/jpeg'


def _gemini_schema(node):
    """
    Recursively adapt a JSON Schema for Gemini's response_schema:
      - Strip `additionalProperties`  (Gemini returns 400 if present)
      - Convert ["type", "null"] unions → {type, nullable: true}  (Gemini syntax)
    """
    if isinstance(node, list):
        return [_gemini_schema(i) for i in node]
    if not isinstance(node, dict):
        return node

    out = {}
    for k, v in node.items():
        if k == 'additionalProperties':
            continue
        if k == 'type' and isinstance(v, list):
            non_null = [t for t in v if t != 'null']
            out['type'] = non_null[0] if non_null else 'string'
            if 'null' in v:
                out['nullable'] = True
        elif isinstance(v, dict):
            out[k] = _gemini_schema(v)
        elif isinstance(v, list) and k not in ('required', 'enum'):
            out[k] = [_gemini_schema(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    if out.get('type') == 'string' and 'enum' in out:
        out['format'] = 'enum'   # Gemini requires format:enum for string enums on stricter models
    return out


def call(prompt: str, image_b64: str, schema: dict, timeout: int = 60) -> str:
    """
    Send a vision prompt + image to Gemini. Returns the raw JSON response string.

    Raises RuntimeError if the ALLOW_CLOUD_OCR egress opt-in or GEMINI_API_KEY
    is not set, or the response carries no usable content (safety block /
    recitation / truncation).
    Raises httpx.HTTPStatusError on non-transient API failures.
    Retries transient 429/5xx with exponential backoff (honors Retry-After).
    """
    if not ALLOW_CLOUD_OCR:
        # Defense in depth: is_configured() already gates every current caller,
        # but refuse here too so no code path can ever ship a facility image to
        # Google without the explicit opt-in.
        raise RuntimeError(
            'Cloud OCR is disabled: set ALLOW_CLOUD_OCR=1 to explicitly permit '
            'sending facility images to Google Gemini (data leaves the facility).'
        )
    if not GEMINI_API_KEY:
        raise RuntimeError('GEMINI_API_KEY is not set')

    payload = {
        'contents': [{
            'parts': [
                {'inline_data': {'mime_type': _mime(image_b64), 'data': image_b64}},
                {'text': prompt},
            ],
        }],
        'generationConfig': {
            'response_mime_type': 'application/json',
            'response_schema': _gemini_schema(schema),
            'temperature': 0,
            'maxOutputTokens': 1024,   # avoid mid-JSON truncation
        },
    }
    # Key goes in a header, not the URL query string: httpx.HTTPStatusError stringifies
    # to include the full request URL, and that string ends up in client-facing error
    # responses and persisted DB rows via api/routes/gauges.py's error handling -- a
    # `?key=...` URL would leak the live key on every transient Gemini failure.
    url = f'{_BASE}/{GEMINI_MODEL}:generateContent'
    headers = {'x-goog-api-key': GEMINI_API_KEY}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError:
            transient = r.status_code == 429 or r.status_code >= 500
            if not transient or attempt == _MAX_ATTEMPTS:
                raise
            retry_after = r.headers.get('Retry-After')
            if retry_after is not None:
                delay = min(float(retry_after), _MAX_RETRY_AFTER)
            else:
                delay = _BASE_DELAY * (_BACKOFF_FACTOR ** (attempt - 1))
                delay += random.uniform(0, _JITTER_MAX)
            _sleep(delay)
            continue
        # Defensive parse: a safety block / recitation / empty result yields no content.
        data = r.json()
        candidates = data.get('candidates') or []
        if not candidates or 'content' not in candidates[0]:
            raise RuntimeError(f'Gemini returned no usable content: {data.get("promptFeedback") or candidates}')
        text = ''.join(p.get('text', '') for p in candidates[0]['content'].get('parts', []))
        if not text.strip():
            raise RuntimeError('Gemini returned an empty response')
        return text
