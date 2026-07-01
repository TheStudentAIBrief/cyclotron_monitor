"""
Gemini Vision API client for gauge OCR and EUR form parsing.

Replaces qwen2.5vl:7b (Ollama) for the two gauge photo endpoints.
The existing prompts and JSON schemas carry over unchanged — they encode
all the PET lab domain knowledge and work better with Gemini than with
a local 7B model.

Setup: get a free API key at https://aistudio.google.com/app/apikey
(no credit card — free tier is 1 500 req/day, 15 req/min).
Set GEMINI_API_KEY in your environment, then restart the API server.
"""
import os

import httpx

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL   = os.environ.get('GEMINI_OCR_MODEL', 'gemini-2.0-flash')
_BASE          = 'https://generativelanguage.googleapis.com/v1beta/models'


def is_configured() -> bool:
    return bool(GEMINI_API_KEY)


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

    Raises RuntimeError if GEMINI_API_KEY is not set.
    Raises httpx.HTTPStatusError on API-level failures (4xx/5xx).
    """
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
    r = httpx.post(
        f'{_BASE}/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}',
        json=payload,
        timeout=timeout,
    )
    r.raise_for_status()
    # Defensive parse: a safety block / recitation / empty result yields no content.
    data = r.json()
    candidates = data.get('candidates') or []
    if not candidates or 'content' not in candidates[0]:
        raise RuntimeError(f'Gemini returned no usable content: {data.get("promptFeedback") or candidates}')
    text = ''.join(p.get('text', '') for p in candidates[0]['content'].get('parts', []))
    if not text.strip():
        raise RuntimeError('Gemini returned an empty response')
    return text
