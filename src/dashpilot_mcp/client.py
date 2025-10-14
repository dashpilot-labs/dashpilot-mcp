"""HTTP client for DashPilot Cloud. One small surface; every tool funnels through here."""
from __future__ import annotations

from typing import Any

import httpx2 as httpx

from . import __version__
from .config import settings


class DashpilotError(Exception):
    """An error the calling agent can read and act on."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class ApiClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None,
                 api_key: str | None = None):
        self._transport = transport
        self._headers = {
            "authorization": f"Bearer {api_key or settings.api_key or ''}",
            "accept": "application/json",
            "user-agent": f"dashpilot-mcp/{__version__}",
        }
        self._async: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        # Created lazily so the connection pool binds to the server's running event
        # loop, not whatever loop (if any) existed when the client was constructed.
        if self._async is None:
            self._async = httpx.AsyncClient(
                base_url=settings.api_url,
                headers=self._headers,
                timeout=settings.timeout_s,
                follow_redirects=False,  # the API never redirects; one would be anomalous
                transport=self._transport,
            )
        return self._async

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            res = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise DashpilotError(
                "backend_unreachable",
                f"DashPilot Cloud is unreachable at {settings.api_url} ({exc.__class__.__name__}). "
                "Start it with `docker compose up -d` or check DASHPILOT_API_URL.",
            ) from exc
        if res.status_code >= 400:
            code, message = f"http_{res.status_code}", res.text[:300]
            try:
                body = res.json()
                detail = body.get("detail") or body.get("error") or {}
                if isinstance(detail, dict):
                    code = detail.get("code") or detail.get("error", {}).get("code") or code
                    message = detail.get("message") or detail.get("error", {}).get("message") or message
            except ValueError:
                pass
            raise DashpilotError(code, message)
        return res.json()

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params={k: v for k, v in (params or {}).items()
                                                        if v is not None})

    async def post(self, path: str, json: dict | None = None) -> Any:
        return await self._request("POST", path, json=json or {})

    async def patch(self, path: str, json: dict | None = None) -> Any:
        return await self._request("PATCH", path, json=json or {})

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    def get_sync(self, path: str) -> Any:
        """Startup-time fetch before the server's event loop exists. Uses a throwaway
        sync client so no async resource is bound outside the loop."""
        try:
            with httpx.Client(base_url=settings.api_url, headers=self._headers,
                              timeout=settings.timeout_s, follow_redirects=False) as http:
                res = http.get(path)
        except httpx.HTTPError as exc:
            raise DashpilotError(
                "backend_unreachable",
                f"DashPilot Cloud is unreachable at {settings.api_url} ({exc.__class__.__name__}). "
                "Start it with `docker compose up -d` or check DASHPILOT_API_URL.",
            ) from exc
        if res.status_code >= 400:
            raise DashpilotError(f"http_{res.status_code}", res.text[:300])
        return res.json()


def money(cents: int) -> str:
    """Human-readable amounts alongside the *_cents fields, so agents quote prices right."""
    return f"${cents / 100:.2f}"


def with_money(payload: Any) -> Any:
    """Recursively add $-formatted twins for every *_cents field in a response."""
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            out[key] = with_money(value)
            if key.endswith("_cents") and isinstance(value, int):
                out[key.removesuffix("_cents")] = money(value)
        return out
    if isinstance(payload, list):
        return [with_money(v) for v in payload]
    return payload
