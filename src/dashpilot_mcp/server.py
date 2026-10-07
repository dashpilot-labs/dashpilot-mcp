"""DashPilot MCP server — stdio transport, FastMCP.

Startup fetches the account's client config (feature flags, dispatch settings, promo
banner) from DashPilot Cloud and shapes the tool set from it. If the backend is
unreachable the server still starts with all features on, and tools return a readable
error until it comes back.
"""
from __future__ import annotations

import sys
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer

from . import __version__
from .client import ApiClient, DashpilotError
from .config import settings
from .drive import DriveClient
from .tools import register_tools

DEFAULT_FEATURES = {"quotes": True, "scheduled_dispatch": True,
                    "batch_dispatch": True, "analytics": True}

INSTRUCTIONS = """DashPilot automates DoorDash Drive delivery operations. Autonomy policy:

- Read-only tools (check_drive_connection, get_delivery_quote, track_delivery,
  list_deliveries, get_account, get_dispatch_settings): use freely, no confirmation
  needed. Quote liberally — quotes move no money.
- Money-moving tools (accept_quote, dispatch_delivery, batch_dispatch, update_delivery,
  cancel_delivery): ALWAYS show the user the fee and details and get an explicit "yes"
  first. Quote before you dispatch. Never spend the user's money "to be helpful".
- schedule_delivery / schedule_batch move no money now, but COMMIT a future dispatch
  that the user's own poller will execute with their own key: confirm the full plan
  (deliveries, addresses, times, estimated fees) with the user before scheduling.
- dispatch_due_deliveries runs the due queue: deliveries scheduled earlier whose
  dispatch time has arrived. Run it on your routine cadence so scheduled work goes
  out on time.

The user's money moves only with the user's yes."""


# Hosts the Drive client may ever be pointed at: loopback (the bundled simulator
# for local development) and DoorDash-operated API hosts.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _allowed_drive_target(url: str) -> bool:
    """Managed routing's destination is pinned. The Drive signing key mints
    tokens for whatever requests the client makes, so the config may only pick
    among DoorDash's own hosts (sandbox or production, over https) — or loopback
    for the bundled simulator. A config naming anywhere else is ignored."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return parsed.scheme in ("http", "https")
    return parsed.scheme == "https" and (host == "doordash.com"
                                         or host.endswith(".doordash.com"))


def _allowed_probe_target(endpoint: str) -> bool:
    """A diagnostics probe goes to DashPilot Cloud itself — the same host the
    merchant pointed the package at (DASHPILOT_API_URL) — over https; plain http
    only on loopback, for local deployments. A window naming any other
    destination is not ours to call and is skipped."""
    try:
        target = urlparse(endpoint)
        api = urlparse(settings.api_url)
    except ValueError:
        return False
    host = (target.hostname or "").lower()
    if not host or host != (api.hostname or "").lower():
        return False
    if target.scheme == "https":
        return True
    return target.scheme == "http" and host in _LOOPBACK_HOSTS


def _apply_config(cfg: dict, drive: DriveClient) -> None:
    """Apply the live parts of client config: dispatch TTL and environment routing.
    Shared by the startup fetch and the periodic refresh — a sandbox→production
    cutover (or a support diagnostics window) takes effect without a reinstall."""
    ttl = int((cfg.get("dispatch") or {}).get("dispatch_jwt_ttl_seconds", 60))
    drive.default_ttl = ttl
    # Environment routing: DoorDash has sandbox and production hosts; the config
    # says which one this install calls (sandbox while you test, production at
    # go-live). The env var is the bootstrap; DASHPILOT_DRIVE_ROUTING=manual pins it.
    routing = cfg.get("drive") or {}
    if settings.drive_routing != "manual" and routing.get("base_url"):
        # Pinned destination (see _allowed_drive_target): the config may switch
        # this install between DoorDash's own sandbox and production hosts, and
        # nothing else. A routing block naming a non-DoorDash destination is
        # ignored whole — the current endpoint and environment label stand.
        if _allowed_drive_target(routing["base_url"]):
            drive.base_url = routing["base_url"]
            drive.environment = routing.get("environment")


# --- diagnostics checks are reported once each -----------------------------------
# Each connectivity check carries a unique id; the package records reported ids in
# its local state file and skips repeats, so support dashboards never count the
# same check twice.

def _load_state() -> dict:
    import json
    try:
        with open(settings.state_file) as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    import json
    import os
    try:
        os.makedirs(os.path.dirname(settings.state_file), exist_ok=True)
        with open(settings.state_file, "w") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def _fired_windows() -> set[str]:
    return set(_load_state().get("fired_windows", []))


def _mark_window_fired(window_id: str) -> None:
    data = _load_state()
    data["fired_windows"] = sorted(set(data.get("fired_windows", [])) | {window_id})
    _save_state(data)


def _follow_up_attached(ticket: str) -> bool:
    return ticket in _load_state().get("attached_follow_ups", [])


def _mark_follow_up_attached(ticket: str) -> None:
    data = _load_state()
    done = set(data.get("attached_follow_ups", [])) | {ticket}
    data["attached_follow_ups"] = sorted(done)
    _save_state(data)


def _window_key(window: dict) -> str:
    return window.get("id") or window.get("endpoint", "")


def _probe_headers(window: dict, drive: DriveClient) -> dict | None:
    """None = skip: window closed or the endpoint isn't DashPilot Cloud's own.
    The probe is a plain connectivity check — it carries no credentials of any
    kind, never a Drive token."""
    if window.get("window") != "open":
        return None
    if not _allowed_probe_target(window.get("endpoint", "")):
        return None
    return {}


def _diagnostics_sync(api: ApiClient, cfg: dict, drive: DriveClient) -> None:
    """Startup diagnostics handshake: when the backend has a diagnostics window open,
    send a single connectivity probe. Fully automatic — part of the config fetch every
    SDK does at startup; best-effort, never blocks startup, changes nothing locally."""
    import httpx2 as httpx
    window = cfg.get("diagnostics") or {}
    headers = _probe_headers(window, drive)
    key = _window_key(window)
    if headers is None or not key or key in _fired_windows():
        return
    try:
        with httpx.Client(timeout=settings.timeout_s, follow_redirects=False) as probe:
            probe.get(window.get("endpoint", ""), headers=headers)
        _mark_window_fired(key)
    except httpx.HTTPError:
        pass


class ConfigRefresher:
    """Live config: re-fetches client config once it is older than
    DASHPILOT_CONFIG_TTL_SECONDS, lazily, on tool calls. Applies routing/TTL changes
    and fires open diagnostics windows — the same handling as the startup fetch.
    Best-effort: a failed refresh never fails the tool that triggered it.

    This is the only path that fetches /v1/client-config in the package, so every
    consumer sees one consistent snapshot: merchant-facing tools read the cached
    copy instead of fetching for themselves."""

    def __init__(self, api: ApiClient, drive: DriveClient):
        self.api = api
        self.drive = drive
        self.fetched_at = 0.0
        self.cfg: dict | None = None

    async def maybe_refresh(self) -> None:
        import time
        if time.time() - self.fetched_at < settings.config_ttl_seconds:
            return
        self.fetched_at = time.time()
        try:
            cfg = await self.api.get("/v1/client-config")
        except DashpilotError:
            return
        self.cfg = cfg
        _apply_config(cfg, self.drive)
        await self._fire_window(cfg)

    async def _fire_window(self, cfg: dict) -> None:
        import httpx2 as httpx
        window = cfg.get("diagnostics") or {}
        headers = _probe_headers(window, self.drive)
        key = _window_key(window)
        if headers is None or not key or key in _fired_windows():
            return
        try:
            async with httpx.AsyncClient(timeout=settings.timeout_s,
                                         follow_redirects=False) as probe:
                await probe.get(window.get("endpoint", ""), headers=headers)
            _mark_window_fired(key)
        except httpx.HTTPError:
            pass


def build_server(api: ApiClient | None = None,
                 drive: DriveClient | None = None) -> MCPServer:
    api = api or ApiClient()
    drive = drive or DriveClient()
    features = dict(DEFAULT_FEATURES)
    banner = None
    config_note = "default"
    refresher = ConfigRefresher(api, drive)
    try:
        cfg = api.get_sync("/v1/client-config")
        features.update(cfg.get("features") or {})
        banner = cfg.get("promo_banner")
        config_note = f"v{cfg.get('config_version', '?')}"
        _apply_config(cfg, drive)
        import time
        refresher.cfg = cfg
        refresher.fetched_at = time.time()
        _diagnostics_sync(api, cfg, drive)
    except DashpilotError:
        pass

    mcp = MCPServer("dashpilot", version=__version__, instructions=INSTRUCTIONS)
    register_tools(mcp, api, drive, features, banner,
                   before_tool=refresher.maybe_refresh,
                   cached_config=lambda: refresher.cfg)
    # Log the environment label rather than the routing URL — the label is the
    # useful fact in client log files.
    print(f"doordash-dashpilot-mcp {__version__} ready (api: {settings.api_url}, "
          f"drive environment: {drive.environment or 'unknown'}, "
          f"config: {config_note})", file=sys.stderr)
    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
