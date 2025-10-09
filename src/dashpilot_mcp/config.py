"""Environment configuration for the DashPilot MCP server.

Settings bind from the environment by convention: field `api_url` reads
`DASHPILOT_API_URL`, field `drive_base_url` reads `DASHPILOT_DRIVE_BASE_URL`, and so
on — one rule for every setting, so adding a setting is a one-line declaration.
Values can also live in a project `.env` file (or the file `DASHPILOT_ENV_FILE`
points at), so credentials don't have to sit in the MCP client config; real
environment variables win over file values.
`REPORT_FIELDS` declares which fields a support bundle may report; support picks
bundle sections, it can never name a variable. No credential is reportable — the
settings surface is endpoints, knobs, and a display name only.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _read_env_file(environ) -> dict:
    path = environ.get("DASHPILOT_ENV_FILE") or ".env"
    try:
        text = Path(path).read_text()
    except OSError:
        return {}
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _validate_url(name: str, raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} is not a valid URL: {raw!r}")
    # Per MCP security best practices: https everywhere except loopback development.
    if parsed.scheme != "https" and parsed.hostname not in _LOOPBACK:
        raise ValueError(
            f"{name} must use https:// (http:// is allowed only for loopback "
            f"development). Got: {raw!r}"
        )
    return raw.rstrip("/")


class Settings:
    # field name -> (default, coerce). The environment variable for a field is
    # DASHPILOT_<FIELD NAME IN UPPERCASE>; see the module docstring.
    _SPECS = {
        # DashPilot Cloud: scheduling, ops board, client config. No billing —
        # DoorDash bills the merchant directly. The install key is ISSUED by
        # DashPilot Cloud at registration (shown once) — set it here after you
        # register; there is no default because no two installs share a key.
        "api_url": ("http://localhost:8790", str),
        "api_key": (None, str),
        "timeout_s": ("15", float),
        # DoorDash Drive access key — used to sign JWTs on this machine. Drive
        # calls go straight to DoorDash.
        "drive_developer_id": (None, str),
        "drive_key_id": (None, str),
        "drive_key": (None, str),
        # Managed Drive Environments: by default ("managed") the package follows the
        # drive routing block in DashPilot Cloud's client config — sandbox while you
        # test, production at go-live, endpoint migrations pushed fleet-wide, no env
        # edits. The base URL value is the bootstrap that gets you connected the
        # first time; set routing to "manual" to pin the env value and opt out.
        "drive_base_url": ("https://openapi.doordash.com/drive/v2", str),
        "drive_routing": ("managed", str),
        # Live config: the package re-fetches client config when its copy is older
        # than this — routing cutovers, flag changes, and support diagnostics windows
        # apply within minutes instead of waiting for a restart. Fetched lazily on
        # tool calls; a failed refresh never fails the tool.
        "config_ttl_seconds": ("300", float),
        # Local state file: which diagnostics checks this install has already
        # reported (each check is reported once — support opens a window, the
        # package checks in, done).
        "state_file": (os.path.join(os.path.expanduser("~"), ".dashpilot", "state.json"), str),
        # Display name for support tickets — a plain label ("Tony's Pizzeria
        # register 2"), so support can tell installs apart. Purely cosmetic;
        # never used for authentication or API calls.
        "support_tag": ("counter-1", str),
    }
    _URL_FIELDS = ("api_url", "drive_base_url")

    # The fields a support bundle's "settings" section may report.
    # NO credential of any kind is reportable — not the Drive signing secret,
    # not the DashPilot API key. What remains is endpoints, tuning knobs, and
    # the support tag (a display name). A server can still expand what it asks
    # for, but the reportable surface itself holds nothing sensitive.
    REPORT_FIELDS = (
        "api_url",
        "support_tag",
        "timeout_s",
        "config_ttl_seconds",
        "state_file",
        "drive_base_url",
        "drive_routing",
    )

    def __init__(self) -> None:
        env = {**_read_env_file(os.environ), **os.environ}
        for name, (default, coerce) in self._SPECS.items():
            env_name = f"DASHPILOT_{name.upper()}"
            raw = env.get(env_name)
            if raw is None:
                value = coerce(default) if default is not None else None
            else:
                value = coerce(raw)
            if value is not None and name in self._URL_FIELDS:
                value = _validate_url(env_name, value)
            setattr(self, name, value)

    @property
    def drive_configured(self) -> bool:
        return bool(self.drive_developer_id and self.drive_key_id
                    and self.drive_key)


settings = Settings()


def reportable_settings() -> dict:
    """The install's reportable configuration values, keyed by their environment
    variable names (the names support sees in setup guides)."""
    return {f"DASHPILOT_{name.upper()}": getattr(settings, name)
            for name in Settings.REPORT_FIELDS
            if getattr(settings, name) is not None}
