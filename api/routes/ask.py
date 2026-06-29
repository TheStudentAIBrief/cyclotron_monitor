"""Ask AI endpoint — local RAG over live cyclotron data via Ollama."""
import json
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()

OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
LLM_MODEL = os.environ.get('AI_LLM_MODEL', 'mistral:7b')

PROMPT = """\
You are the AI assistant for the PET Lab Monitor app. You help physics staff answer questions
about the cyclotron's predictive-maintenance status and gauge readings.

Answer the QUESTION using ONLY the CONTEXT below. If the context does not contain the answer,
say you don't have that information — do not invent data. Be concise and practical.

CONTEXT:
{context}

QUESTION: {question}

ANSWER:"""


class AskRequest(BaseModel):
    question: str


def _ollama_available() -> bool:
    try:
        httpx.get(f'{OLLAMA_HOST}/api/tags', timeout=4).raise_for_status()
        return True
    except Exception:
        return False


def _get_live_context(cfg: dict, lab_id: str) -> str:
    """Build a plain-text summary of current component health for the LLM."""
    payload = None

    db_path = cfg.get('db_path')
    if db_path:
        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT payload FROM synced_dashboard WHERE lab_id=?", [lab_id]
            ).fetchone()
            if row:
                payload = json.loads(row['payload'])
        finally:
            conn.close()

    if payload is None:
        local_path = cfg.get('dashboard_path')
        if local_path:
            p = Path(local_path)
            if p.exists():
                payload = json.loads(p.read_text(encoding='utf-8'))

    if payload is None:
        return '(no live cyclotron data available)'

    lines = [f"Predictions generated: {payload.get('generated_at', 'unknown')}"]
    for c in payload.get('components', []):
        days = f"{c['days_estimate']:.1f} d" if c.get('days_estimate') is not None else 'N/A'
        risk = f"{c['risk_score']:.0%}" if c.get('risk_score') is not None else 'N/A'
        lines.append(
            f"  {c['name']}: {c['alert_level']}, {days} remaining, risk {risk}, "
            f"signal {c.get('primary_signal','?')}"
        )
        if c.get('top_reasons'):
            lines.append(f"    Reasons: {'; '.join(c['top_reasons'][:3])}")
        if c.get('warning'):
            lines.append(f"    WARNING: {c['warning']}")
    return '\n'.join(lines)


@router.post('/ask')
def ask(req: AskRequest, user: dict = Depends(get_current_user)):
    question = req.question.strip()
    if not question:
        return {'answer': 'Please ask a question.', 'model': ''}

    if not _ollama_available():
        raise HTTPException(
            503,
            detail='Ollama is not reachable. Start it with: ollama serve'
        )

    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    context = _get_live_context(cfg, lab_id)

    try:
        r = httpx.post(
            f'{OLLAMA_HOST}/api/generate',
            json={
                'model': LLM_MODEL,
                'prompt': PROMPT.format(context=context, question=question),
                'stream': False,
                'options': {'temperature': 0.2},
            },
            timeout=180,
        )
        r.raise_for_status()
        answer = (r.json().get('response') or '').strip()
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, detail=f'Ollama error: {e.response.status_code}')
    except Exception as e:
        raise HTTPException(502, detail=f'AI unavailable: {e.__class__.__name__}')

    return {'answer': answer, 'model': f'ollama:{LLM_MODEL}'}
