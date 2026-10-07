"""OpenRouter adapter shared by the report writer and the Jev grader. Strict JSON schema only."""
from __future__ import annotations

import json
import time

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


def openrouter_json(*, api_key: str | None, model: str, system: str, user: str, schema: dict, schema_name: str,
                    max_tokens: int, effort: str | None = None, data_collection: str = "deny",
                    timeout: float = 300, max_retries: int = 3, transport=None, sleep=time.sleep) -> tuple[dict, dict]:
    """Returns (parsed_json, usage). `data_collection: deny` keeps requests off providers that store or train
    on prompts; `require_parameters` keeps them off providers that would ignore the schema."""
    if not api_key:
        raise LLMError("OPENROUTER_API_KEY is not set (put it in .env)")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        "provider": {"require_parameters": True, "data_collection": data_collection},
        "usage": {"include": True},
    }
    if effort:
        body["reasoning"] = {"effort": effort}
    headers = {"Authorization": f"Bearer {api_key}", "X-Title": "sales-call-coach"}
    attempt = 0
    with httpx.Client(timeout=timeout, transport=transport) as http:
        while True:
            try:
                resp = http.post(OPENROUTER_URL, json=body, headers=headers)
            except httpx.TransportError as e:
                if attempt >= max_retries:
                    raise LLMError(f"OpenRouter network error: {e}") from e
                sleep(min(2 ** attempt, 30)); attempt += 1
                continue
            if resp.status_code in RETRYABLE and attempt < max_retries:
                ra = resp.headers.get("retry-after")
                sleep(min(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2 ** attempt, 60)); attempt += 1
                continue
            break
    if resp.status_code in (401, 403):
        raise LLMError(f"OpenRouter rejected the key (HTTP {resp.status_code})")
    if resp.status_code == 402:
        raise LLMError("OpenRouter: out of credit (HTTP 402)")
    if resp.status_code >= 400:
        raise LLMError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if data.get("error"):
        raise LLMError(f"OpenRouter error: {data['error']}")
    choice = data["choices"][0]
    u = data.get("usage") or {}
    usage = {"model": data.get("model", model), "provider": data.get("provider"),
             "input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens"),
             "cost_usd": u.get("cost"), "stop_reason": choice.get("finish_reason")}
    if choice.get("finish_reason") == "length":
        raise LLMError("Model hit max_tokens; raise max_tokens in config")
    text = (choice.get("message") or {}).get("content")
    if not text:
        raise LLMError("Model returned no content")
    try:
        return json.loads(text), usage
    except ValueError as e:
        raise LLMError("Model returned invalid JSON despite the schema") from e
