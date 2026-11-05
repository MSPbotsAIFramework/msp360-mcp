# msp360-mcp

MCP server for **MSP360 Managed Backup Service (MBS)**, built for the MSPbots Agent Platform.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Overview

Implements the [Model Context Protocol](https://modelcontextprotocol.io/) (Streamable HTTP
transport) and exposes **10 tools** over MSP360's Managed Backup Service (MBS) REST API:

- "Does this MSP360 connection actually work?" → `msp360_provider_login`
- "Is backup healthy across every company and computer we manage?" → `msp360_get_monitoring`
  (optionally narrowed to one backup user with `user_id`)
- "What customer companies does this provider account manage?" → `msp360_list_companies`
- "Which computers does this backup user have, and are they backing up correctly?" →
  `msp360_list_user_computers(user_id)` — one row per computer, with status, last backup date,
  and contributing plans; the audit-report view for one user (`user_id` sourced from
  `msp360_list_users`)
- "What plans are configured on this computer?" → `msp360_list_plans(hid)`
- "What's this plan's current status / recent run history?" → `msp360_get_plan_info`,
  `msp360_get_plan_history`
- "Start / cancel a run of this plan" → `msp360_start_plan`, `msp360_stop_plan`
- "List the domains/apps under the MSP360 portal, with their storage usage and license counts"
  → `msp360_list_domains` (a CloudBackup-tagged resource, distinct from computers or companies)

| Category | Tools | Annotations |
|---|---|---|
| Auth | `msp360_provider_login` | readOnly |
| Monitoring | `msp360_get_monitoring`, `msp360_list_user_computers` | readOnly |
| Companies | `msp360_list_companies` | readOnly |
| Plans | `msp360_list_plans`, `msp360_get_plan_info`, `msp360_get_plan_history` | readOnly |
| | `msp360_start_plan` | not destructive, not idempotent |
| | `msp360_stop_plan` | destructive, idempotent |
| Domains | `msp360_list_domains` | readOnly |

`msp360_provider_login` never returns the vendor's `access_token` — it reports only
`{"authenticated": true, "expires_in": <seconds or null>}`, or a structured error. It is a
connectivity check, not a session manager: it performs its own fresh vendor login on every call
and does not read or populate the token cache described below (see docs/tickets/PRD-21604.md).

Every tool is read-only except `msp360_start_plan` / `msp360_stop_plan`, which trigger or cancel a
run (fire-and-forget — they report that MSP360 accepted the action, not that the run has
finished).

## Authentication headers

Every request to `/mcp` must include the following HTTP headers:

| Header | Type | Required | Description | Example |
|---|---|---|---|---|
| `X-Msp360-Username` | string | Yes | MSP360 Managed Backup provider-account username. MSP360 admin portal → Settings → API credentials. | `X-Msp360-Username: provider@example.com` |
| `X-Msp360-Password` | string | Yes | MSP360 Managed Backup provider-account password. Same place as above. | `X-Msp360-Password: your_password_here` |

This server exchanges the username/password for a bearer token internally
(`POST /api/Provider/Login`) on first use and caches it per credential pair until it expires — the
Agent never sees or passes around a token.

Header names are case-insensitive. A request to `/mcp` without both headers gets `401` with the
list of expected headers.

## Run locally

```bash
uv sync
uv run msp360-mcp              # listens on 0.0.0.0:8080
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "X-Msp360-Username: $MSP360_USERNAME" -H "X-Msp360-Password: $MSP360_PASSWORD" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Environment variables (all optional, unknown ones ignored): `MCP_HTTP_HOST` (default `0.0.0.0`),
`MCP_HTTP_PORT` (default `8080`). Credentials are never read from the environment.

## Docker

```bash
docker build --platform linux/amd64 -t msp360-mcp .
docker run --rm -p 8080:8080 msp360-mcp
```

## Development

```bash
uv sync
uv run pytest -q
```

Code is in `src/msp360_mcp/` (one module per resource under `tools/`), tests in `tests/`.

## Known gaps / implementation notes

- **No MSP360 test account was available at build time.** All read tools and the error-mapping
  paths are covered by mocked unit tests; test cases that need a real response shape or a real
  account (multi-tenant token-cache isolation, and confirming the exact start/stop/info/history
  REST paths) are marked `manual · needs a real vendor account` in
  `docs/tickets/PRD-21273.ac.json`. This does not block delivery per the team's vendor-mcp SOP.
- **`/Plans/{id}/info`, `/Plans/{id}/history`, `/Plans/{id}/start`, and `/Plans/{id}/stop` are
  corroborated by a third-party reference implementation**
  (`github.com/WYRE-AI/msp360-mcp`), not directly readable vendor text — MSP360's own doc-site
  search index does not surface these specific sub-resource paths, though the "info"/"history"
  PowerShell cmdlets plainly call something equivalent. If a live account later shows a different
  path, only `src/msp360_mcp/client.py`'s path strings change — the tool contracts here do not.
- **This repository is shared with six sibling tickets** (PRD-21604, PRD-21605 — both implemented
  in this build — plus PRD-21606–21609, each adding one more separate MSP360 tool:
  `msp360_list_users`, `msp360_list_user_computers`, `msp360_list_packages`,
  `msp360_list_domains`). None of them overlap with the monitoring/plan-control tools in this
  README; coordinate changes to shared files (this README, `pyproject.toml`) through the repo's
  `dev-lock`.
- **No MSP360 test account was available at build time for `msp360_list_companies` either** — same
  gap as the rest of this README's "Known gaps" entry above. The `CompanyModel.Id` field is read as
  `company_id` on the assumption that it is the "CompanyID" field the ticket names under a
  different vendor-schema name (design doc PRD-21605 OQ1); `StorageLimit`'s unit (OQ2) and
  `LicenseSettings`'s enum labels (OQ3) are likewise unconfirmed and passed through verbatim. A spot
  check against a live account would resolve all three.
- **`msp360_get_monitoring`'s pagination is applied client-side** (`limit`/`offset`, capped at
  200 records / 20,000 characters) on top of whatever the vendor's `GET /api/Monitoring` returns
  in one shot — the vendor's own native paging contract is not confirmed from primary text. If a
  real account later shows server-side paging, only the internal request handling changes.
- No code from `github.com/WYRE-AI/msp360-mcp` or any other third-party project is included here —
  see [NOTICE](NOTICE).

## License

Copyright 2026 MSPbots.ai. Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE)
and [NOTICE](NOTICE).

MSP360 is a trademark of its owner. This project is not affiliated with or endorsed by the owner
of MSP360.
