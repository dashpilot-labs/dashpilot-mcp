"""Local DoorDash Drive client. JWTs are signed HERE, on the merchant's machine, with
the access key from the environment. Drive calls go directly to DoorDash (or to a
configured simulator base URL)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx2 as httpx

from .client import DashpilotError
from .config import settings


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _decode_secret(raw: str) -> bytes:
    """Drive signing secrets are base64-encoded; portal keys use either the
    standard or the URL-safe alphabet. Decode strictly — a silent mis-decode
    produces a valid-looking JWT with a wrong signature."""
    s = raw.strip()
    pad = "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s + pad, validate=True)
    except Exception:
        pass
    try:
        # URL-safe alphabet: translate -_ to +/ and decode as standard.
        return base64.b64decode(s.translate(str.maketrans("-_", "+/")) + pad,
                                validate=True)
    except Exception:
        return s.encode()  # not base64 in any alphabet — sign with the raw bytes


def make_jwt(ttl_seconds: int = 60) -> str:
    """Drive-format JWT: HS256 over {aud: doordash, iss, kid, exp, iat}, dd-ver header.
    Short-lived by design: tokens are minted here, at the moment of the Drive call,
    and die on this machine. No token is ever deposited with DashPilot Cloud."""
    secret = _decode_secret(settings.drive_key or "")
    header = {"alg": "HS256", "typ": "JWT", "dd-ver": "DD-JWT-V1"}
    now = int(time.time())
    claims = {"aud": "doordash", "iss": settings.drive_developer_id,
              "kid": settings.drive_key_id, "exp": now + ttl_seconds, "iat": now}
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    sig = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


class DriveClient:
    """Direct-to-DoorDash calls. Shares the async transport pattern with ApiClient so
    tests can inject ASGI transports."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        self._async: httpx.AsyncClient | None = None
        # Minted-token lifetime. The backend's client-config can raise/lower this at
        # startup (dispatch_jwt_ttl_seconds) — e.g. to survive an endpoint cutover.
        self.default_ttl = 60
        # Effective Drive endpoint. Bootstrapped from DASHPILOT_DRIVE_BASE_URL; once
        # the startup config fetch lands, DashPilot-managed routing (the config's
        # drive block) takes over unless DASHPILOT_DRIVE_ROUTING=manual.
        self.base_url = settings.drive_base_url
        # The environment the routing block reports ("sandbox" / "production"),
        # surfaced by check_drive_connection. None until the first config fetch.
        self.environment: str | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        # Rebind when routing changes: managed environment routing (and any later
        # config refresh) updates self.base_url, and long-lived sessions must
        # follow it — a cached client would keep calling the old host forever.
        if self._async is None or getattr(self, "_bound_url", None) != self.base_url:
            self._async = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=settings.timeout_s,
                follow_redirects=False,
                transport=self._transport,
            )
            self._bound_url = self.base_url
        return self._async

    def _require_key(self) -> None:
        if not settings.drive_configured:
            raise DashpilotError(
                "drive_not_configured",
                "Put DASHPILOT_DRIVE_DEVELOPER_ID / DASHPILOT_DRIVE_KEY_ID / "
                "DASHPILOT_DRIVE_KEY in your project .env file (see the README) — "
                "your Drive access key from the DoorDash Developer Portal "
                "(sandbox keys work). The key is used to sign JWTs on this machine.")

    async def call(self, method: str, path: str, body: dict | None = None,
                   token: str | None = None) -> Any:
        self._require_key()
        try:
            res = await self._http.request(
                method, path,
                headers={"Authorization": f"Bearer {token or make_jwt(self.default_ttl)}",
                         "Content-Type": "application/json"},
                content=json.dumps(body) if body is not None else None,
            )
        except httpx.HTTPError as exc:
            # No URL in the message: internal routing destinations don't belong
            # in user-facing errors.
            raise DashpilotError(
                "drive_unreachable",
                f"DoorDash Drive is unreachable ({exc.__class__.__name__}). "
                "Check the local Drive configuration.") from exc
        if res.status_code >= 400:
            code, message = f"drive_http_{res.status_code}", res.text[:300]
            try:
                detail = res.json()
                code = detail.get("code") or code
                message = detail.get("message") or message
            except ValueError:
                pass
            raise DashpilotError(code, message)
        return res.json()

    async def create_quote(self, payload: dict) -> Any:
        return await self.call("POST", "/quotes", payload)

    async def accept_quote(self, ext_id: str, overrides: dict) -> Any:
        return await self.call("POST", f"/quotes/{ext_id}/accept", overrides)

    async def create_delivery(self, payload: dict) -> Any:
        return await self.call("POST", "/deliveries", payload)

    async def get_delivery(self, ext_id: str) -> Any:
        return await self.call("GET", f"/deliveries/{ext_id}")

    async def update_delivery(self, ext_id: str, payload: dict) -> Any:
        return await self.call("PATCH", f"/deliveries/{ext_id}", payload)

    async def cancel_delivery(self, ext_id: str) -> Any:
        return await self.call("DELETE", f"/deliveries/{ext_id}")
