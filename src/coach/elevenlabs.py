"""ElevenLabs Agents API adapter. Only HTTP + response shaping lives here; no grading logic."""
from __future__ import annotations

import time
from typing import Iterator

import httpx


class ElevenLabsError(Exception):
    pass


class CredentialsError(ElevenLabsError):
    """401/403 — bad key or missing permission. Never retried."""


class NotFoundError(ElevenLabsError):
    """404 — conversation or agent unavailable (deleted, retention expired, wrong workspace host)."""


class SchemaError(ElevenLabsError):
    """422 or a response that doesn't have the fields we rely on."""


class RequestCapReached(ElevenLabsError):
    pass


RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ElevenLabsClient:
    def __init__(self, api_key: str, base_url: str, *, timeout: float = 30, max_retries: int = 4,
                 min_interval: float = 0.2, max_requests: int = 3000,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep, key_name: str = "ELEVENLABS_API_KEY"):
        if not api_key:
            raise CredentialsError(f"{key_name} is not set (put it in .env)")
        self.key_name = key_name
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  headers={"xi-api-key": api_key, "accept": "application/json"})
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.max_requests = max_requests
        self.requests_made = 0
        self._last = 0.0
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    # -- core request with bounded retries -------------------------------------------------
    def _request(self, method: str, path: str, **kw) -> dict:
        attempt = 0
        while True:
            if self.requests_made >= self.max_requests:
                raise RequestCapReached(f"Request cap of {self.max_requests} reached for this run")
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            self.requests_made += 1
            try:
                resp = self._http.request(method, path, **kw)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt >= self.max_retries:
                    raise ElevenLabsError(f"{method} {path}: network error after {attempt + 1} attempts: {e}") from e
                self._sleep(min(2 ** attempt, 30))
                attempt += 1
                continue
            if resp.status_code in (401, 403):
                raise CredentialsError(f"{method} {path}: HTTP {resp.status_code} — check {self.key_name} permissions/host"
                                       f" ({resp.text[:200]})")
            if resp.status_code == 404:
                raise NotFoundError(f"{method} {path}: not found")
            if resp.status_code == 422:
                raise SchemaError(f"{method} {path}: HTTP 422 {resp.text[:300]}")
            if resp.status_code in RETRYABLE_STATUS:
                if attempt >= self.max_retries:
                    raise ElevenLabsError(f"{method} {path}: HTTP {resp.status_code} after {attempt + 1} attempts")
                retry_after = resp.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
                except ValueError:
                    delay = min(2 ** attempt, 30)
                self._sleep(min(delay, 120))
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise ElevenLabsError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:300]}")
            try:
                return resp.json()
            except ValueError as e:
                raise SchemaError(f"{method} {path}: response is not JSON") from e

    # -- endpoints ---------------------------------------------------------------------------
    def list_agents(self) -> list[dict]:
        agents, cursor = [], None
        while True:
            params = {"page_size": 100, **({"cursor": cursor} if cursor else {})}
            page = self._request("GET", "/v1/convai/agents", params=params)
            agents += page.get("agents", [])
            if not page.get("has_more") or not page.get("next_cursor"):
                return agents
            cursor = page["next_cursor"]

    def get_agent(self, agent_id: str) -> dict:
        return self._request("GET", f"/v1/convai/agents/{agent_id}")

    def update_agent_criteria(self, agent_id: str, criteria: list[dict], version_description: str) -> dict:
        body = {"platform_settings": {"evaluation": {"criteria": criteria}},
                "version_description": version_description}
        return self._request("PATCH", f"/v1/convai/agents/{agent_id}", json=body)

    def create_branch(self, agent_id: str, *, parent_version_id: str, name: str, description: str,
                      conversation_config: dict) -> dict:
        return self._request("POST", f"/v1/convai/agents/{agent_id}/branches", json={
            "parent_version_id": parent_version_id, "name": name, "description": description,
            "conversation_config": conversation_config})

    def set_first_message(self, agent_id: str, branch_id: str, text: str, description: str) -> dict:
        """Partial update: only conversation_config.agent.first_message (verified to leave everything else intact)."""
        return self._request("PATCH", f"/v1/convai/agents/{agent_id}", params={"branch_id": branch_id},
                             json={"conversation_config": {"agent": {"first_message": text}}, "version_description": description})

    def merge_preview(self, agent_id: str, source_branch_id: str, target_branch_id: str) -> dict:
        return self._request("GET", f"/v1/convai/agents/{agent_id}/branches/{source_branch_id}/merge-preview",
                             params={"target_branch_id": target_branch_id})

    def merge_branch(self, agent_id: str, source_branch_id: str, target_branch_id: str, *, archive: bool = True) -> dict:
        return self._request("POST", f"/v1/convai/agents/{agent_id}/branches/{source_branch_id}/merge",
                             params={"target_branch_id": target_branch_id}, json={"archive_source_branch": archive})

    def set_traffic(self, agent_id: str, split: dict[str, float]) -> dict:
        """split: {branch_id: percentage}; must total 100."""
        if abs(sum(split.values()) - 100) > 1e-6:
            raise ValueError("traffic percentages must total 100")
        return self._request("POST", f"/v1/convai/agents/{agent_id}/deployments", json={"deployment_request": {
            "requests": [{"branch_id": b, "deployment_strategy": {"type": "percentage", "traffic_percentage": p}}
                         for b, p in split.items()]}})

    def list_conversations_page(self, *, agent_id: str, after_unix: int, before_unix: int | None,
                                page_size: int, cursor: str | None,
                                criteria_ids: list[str]) -> dict:
        params: list[tuple[str, str | int]] = [
            ("agent_id", agent_id), ("call_start_after_unix", after_unix),
            ("page_size", min(page_size, 100)), ("sort_direction", "asc"),
        ]
        if before_unix is not None:
            params.append(("call_start_before_unix", before_unix))
        if cursor:
            params.append(("cursor", cursor))
        params += [("evaluation_criteria_ids", cid) for cid in criteria_ids]
        page = self._request("GET", "/v1/convai/conversations", params=params)
        if "conversations" not in page or "has_more" not in page:
            raise SchemaError("list conversations: missing 'conversations'/'has_more'")
        return page

    def iter_conversations(self, *, cursor: str | None = None, **kw) -> Iterator[tuple[list[dict], str | None]]:
        """Yields (conversations, next_cursor) per page; next_cursor is None on the last page."""
        while True:
            page = self.list_conversations_page(cursor=cursor, **kw)
            cursor = page.get("next_cursor") if page.get("has_more") else None
            yield page["conversations"], cursor
            if not cursor:
                return

    def get_conversation(self, conversation_id: str) -> dict:
        data = self._request("GET", f"/v1/convai/conversations/{conversation_id}")
        if "transcript" not in data or "status" not in data:
            raise SchemaError(f"conversation {conversation_id}: missing transcript/status")
        return data

    def run_analysis(self, conversation_id: str) -> dict:
        """Re-runs analysis with the agent's *current* criteria (used to backfill a new checklist)."""
        return self._request("POST", f"/v1/convai/conversations/{conversation_id}/analysis/run")
