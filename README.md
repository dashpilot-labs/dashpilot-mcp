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

The MCP signs Drive JWTs locally and calls DoorDash directly for quotes, dispatch,
tracking, updates, and cancels. The tokens it mints go to DoorDash and nowhere else,
with one disclosed exception: the once-daily credential health check-in sends a single
60-second token to your DashPilot Cloud deployment's health endpoint (details below).
DashPilot Cloud receives only (a) usage reports that feed your
ops board, and (b) for *scheduled* deliveries, the **unsigned payload** —
when it comes due, this package fetches the due queue, mints a fresh 60-second JWT
right here, and dispatches directly.

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
  environment you're on. What managed mode delegates is endpoint *selection*, and
  the selection is pinned: the config may only switch this install between
  DoorDash's own sandbox and production hosts (or the bundled loopback simulator)
  — a routing block naming anywhere else is ignored, so a config change can never
  point your signed Drive requests at a host DoorDash doesn't operate. Installs
  that would rather not delegate at all can set `manual` to pin the bootstrap URL.

First run, ask your agent: *"check my Drive connection"* — it verifies the key with a
side-effect-free signed call and tells you which environment you're on.

## The autonomy model: your money moves only with your yes

The agent is a planner, not a spender. Every tool is annotated in the MCP protocol
(`readOnlyHint` / `destructiveHint`) so your client can auto-approve the safe ones and
always prompt for the rest:

| The agent can do alone | The agent needs your explicit confirmation for |
|---|---|
| Quotes (fee, tax, ETA) — quote liberally | Dispatching anything (`accept_quote`, `dispatch_delivery`, `batch_dispatch`) |
| Live tracking, ops board, account, settings, diagnostics sync (`sync_diagnostics`) | Tip changes and cancels (`update_delivery`, `cancel_delivery`) |
| Planning an event and showing you the full plan | Scheduling it (`schedule_delivery`, `schedule_batch`) — scheduling moves no money now but commits a future dispatch your poller will execute with your key |
| | Uploading diagnostics (`generate_support_bundle`) — the receipt itemizes the sections sent (section names and total size, not field contents) |

`dispatch_due_deliveries` is the executor of confirmed plans: everything it fires was
already confirmed at schedule time, so it needs no new confirmation. Run it on a cadence
and due work just fires.

## What you can ask for

- *"Quote a delivery for order #1001 to 350 5th Ave"* — `get_delivery_quote` (fee, tax, ETA)
- *"Dispatch it"* — `accept_quote`, or `dispatch_delivery` to quote+accept in one step
- *"Schedule the catering run for 6:30pm"* — `schedule_delivery`, then `dispatch_due_deliveries` fires due work with a fresh local JWT
- *"Schedule my launch night: 12 drops, one every 15 minutes from 6pm"* — `schedule_batch` (up to 50, staggered)
- *"Dispatch these 12 office lunches now"* — `batch_dispatch` (up to 25 at once)
- *"Where is order #1001?"* — `track_delivery` (status, Dasher, ETA, tracking URL — straight from DoorDash)
- *"Show today's board"* — `list_deliveries`
- *"Bump the tip on #1001 by $2"* — `update_delivery`
- *"Cancel #1002"* — `cancel_delivery`

Money questions live in your DoorDash developer portal — DoorDash bills you directly;
DashPilot has no billing surface.

The agent is instructed (server-level policy) to always quote the fee and confirm with
you before dispatching — and the tool annotations let your client enforce it, not just
trust it.

## Tools

| Tool | Purpose | Talks to |
|---|---|---|
| `check_drive_connection` | Verify your local Drive key (side-effect-free) | DoorDash Drive |
| `get_delivery_quote` | Delivery fee, tax, ETA — nothing moves yet | DoorDash Drive |
| `accept_quote` | Accept a quote — dispatches a real Dasher | DoorDash Drive |
| `dispatch_delivery` | Quote + accept in one step | DoorDash Drive |
| `schedule_delivery` | Deferred dispatch — stores the unsigned payload only | DashPilot Cloud |
| `schedule_batch` | Schedule a whole event (up to 50, staggered window) | DashPilot Cloud |
| `dispatch_due_deliveries` | Poll the due queue, sign locally (60s JWT), dispatch | Drive + DashPilot Cloud |
| `track_delivery` | Live status, Dasher info, ETA, tracking URL | DoorDash Drive |
| `update_delivery` | Tip / instructions / contact phone | DoorDash Drive |
| `cancel_delivery` | Cancel (Drive cancellation rules apply) | DoorDash Drive |
| `batch_dispatch` | Up to 25 deliveries in one call | DoorDash Drive |
| `list_deliveries` | Operations board + scheduled queue | DashPilot Cloud |
| `get_account` | Business, dispatch count, member since | DashPilot Cloud |
| `get_dispatch_settings` | Feature flags and dispatch settings | DashPilot Cloud |
| `generate_support_bundle` | Diagnostics bundle for a support ticket | DashPilot Cloud |
| `sync_diagnostics` | Routine connectivity sync — part of the session handshake | DashPilot Cloud |
| `delete_install` | Close this install — deletes its DashPilot Cloud data (irreversible) | DashPilot Cloud |

## Security & privacy notes

- stdio transport only; the package opens no network listener.
- Two outbound URLs: `DASHPILOT_API_URL` (DashPilot Cloud) and `DASHPILOT_DRIVE_BASE_URL`
  (DoorDash). Both must be `https://` outside loopback; redirects are never followed.
- Your Drive access key is read from your project `.env` file (or the environment)
  and used to sign JWTs on this machine.
- Crash reporting is structural: when a tool call fails, the package keeps a redacted
  record (tool name, error code, argument *shapes* — strings and numbers are replaced
  by their types, so no address, phone, key, or free text can ride along). Redacted
  records are included in a support bundle when you choose to send one.
- Scheduled deliveries are stored as **unsigned payloads**. No bearer token is ever
  deposited; the backend cannot dispatch anything itself. Due work fires when you run
  `dispatch_due_deliveries` from a machine that has the key — the trade-off for zero
  custody is that something of yours must be awake at dispatch time.
- One standing check-in, disclosed on the security page: once a day the package sends a
  single short-lived (60-second) Drive token to your DashPilot Cloud deployment's health
  endpoint, so a dead credential can be flagged to you — verified and discarded, never
  stored. Your state file records which daily check-in already fired. Beyond that: no
  telemetry, no analytics, no install scripts.

## Development

```bash
uv sync
uv run pytest
DASHPILOT_API_URL=http://localhost:8790 uv run dashpilot-mcp
```

## MCP Registry

mcp-name: io.github.dashpilot-labs/dashpilot-mcp
