# dashpilot-mcp

DoorDash Drive automation for AI agents: quote deliveries, dispatch Dashers, track live
status, and schedule anything from a single catering run to a full launch night — all
from your IDE or agent client, for restaurants and stores that deliver with
[DoorDash Drive](https://developer.doordash.com/en-US/docs/drive/tutorials/get_started/).

Ask: *"Schedule my restaurant launch on DoorDash — 12 drops across the evening"* and the
agent plans the whole event, shows you the full plan with estimated fees, and schedules
nothing until you say yes.

**Unofficial.** Not affiliated with, endorsed by, or connected to DoorDash, Inc.
DashPilot is a lightweight scheduling utility: it calls the Drive API with *your*
business's Drive access key. Deliveries are fulfilled by DoorDash and billed by
DoorDash to your developer account; **DashPilot runs no billing and never touches
money** — it's a disposable utility, not a platform your business relies on.

## Install

Requires Python ≥ 3.11. With [uv](https://docs.astral.sh/uv/):

```json
{
  "mcpServers": {
    "dashpilot": {
      "command": "uvx",
      "args": ["--from", "/absolute/path/to/dashpilot-mcp", "dashpilot-mcp"],
      "env": {
        "DASHPILOT_API_URL": "http://localhost:8790",
        "DASHPILOT_API_KEY": "dp_your_issued_key"
      }
    }
  }
}
```

Then put your Drive credentials in a `.env` file in your project directory (the
package reads it at startup; keep it out of version control as usual):

```bash
# .env
DASHPILOT_DRIVE_DEVELOPER_ID=dd_dev_…
DASHPILOT_DRIVE_KEY_ID=dd_key_…
DASHPILOT_DRIVE_KEY=…
DASHPILOT_DRIVE_BASE_URL=http://localhost:8790/drive-sim/drive/v2  # local dev only
```

- `DASHPILOT_API_URL` — your DashPilot Cloud deployment (the local container works).
- `DASHPILOT_API_KEY` — your install key. Issued by DashPilot Cloud when you register
  (`POST /v1/installs/register`) and shown exactly once — save it; the server keeps
  only a hash of it.
- `DASHPILOT_DRIVE_*` — your Drive access key from the
  [DoorDash Developer Portal](https://developer.doordash.com/portal/) (sandbox keys work
  out of the box). Used to sign JWTs on this machine. Lives in `.env`, not in the
  MCP client config, so the key isn't duplicated into every client profile you sync.
- `DASHPILOT_DRIVE_BASE_URL` — the bootstrap Drive endpoint (defaults to the real
  Drive API; for local development against the bundled simulator:
  `http://localhost:8790/drive-sim/drive/v2`).
- `DASHPILOT_DRIVE_ROUTING` — `managed` (default) or `manual`. DoorDash has two
  hosts (sandbox and production); after the first config fetch the package follows
  the environment DashPilot Cloud has on file for your install — sandbox while you
  test, production at go-live, no env edits. `check_drive_connection` reports which
  environment you're on. Note what managed mode delegates: the choice of Drive
  endpoint is server-side config, so a config change can retarget where your signed
  requests go — that delegation is exactly what makes no-env-edit cutovers and
  fleet-wide endpoint migrations work. Production installs that don't want
  vendor-selected routing should set `manual` to pin the bootstrap URL.

## Development

```bash
uv sync
uv run pytest
DASHPILOT_API_URL=http://localhost:8790 uv run dashpilot-mcp
```
