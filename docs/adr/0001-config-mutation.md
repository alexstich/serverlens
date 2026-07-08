# ADR 0001 — Safe client-driven config changes

Status: **Accepted (prototype)** · Date: 2026-07-07

## Context

`config.yaml` is ServerLens' **trust boundary**. It is not "settings" — it is the
exhaustive whitelist of what any client (and the LLM behind it) may ever see:
which tables, which columns (with explicit `denied_fields` like `password_hash`,
`api_key`, `reset_token`), which logs, which config files, which services.

Operationally this whitelist drifts. A migration adds a table or a column and the
whitelist has to catch up, otherwise the AI can't answer. Editing the file by hand
over SSH each time is friction, so the request was: *let the client change the
config through MCP.*

The constraints that shape the answer:

- The service runs as the unprivileged `serverlens` user, sandboxed by systemd
  (`ProtectSystem=strict`, `ReadOnlyPaths=/`). It **cannot write** `/etc/serverlens/config.yaml`
  by design — that file is `640 root:serverlens`.
- The config is loaded once at boot and held in memory.
- **ServerLens reads attacker-influenced data**: log lines and DB rows. That is a
  live prompt-injection surface — content an outsider can plant, that the LLM then reads.

## Decision

Split configuration change into two planes with different identities and trust.

### Data-plane (read-only, in the MCP channel) — `config_suggest`

A new read-only MCP tool. It introspects the live DB schema through the same
read-only user and returns a **proposed patch** (YAML), auto-sorting any
sensitive-looking column into `denied_fields`. It writes nothing. The LLM can
*prepare* a change; it cannot *make* one.

### Control-plane (privileged, out of band) — `serverlens-admin`

A separate binary, run by a human over SSH via `sudo`, never part of the
read-only service process. Flow: `propose` (validate + stage a diff) →
`apply --otp <code>` (second factor → merge → validate → reload).

Key properties:

- **Second factor out of the LLM's reach.** Apply requires a TOTP code from the
  operator's authenticator. The secret lives root-only on the server
  (`/etc/serverlens/admin.totp`, mode 600); it is not on the developer machine
  and never enters the MCP/LLM context. A shared password checked inside the MCP
  process was rejected — it would sit next to the SSH key and flow through the
  model.
- **Additive only.** A patch may only *add* log/config sources, DB tables/columns
  and system service/stack entries. It can never remove or rewrite what exists.
- **Hard policy (`serverlens/admin/policy.py`), unbypassable via the tool:**
  - `auth`, `audit`, `rate_limiting`, `server` sections are untouchable.
  - A column whose name looks sensitive (`*password*`, `*token*`, `*secret*`,
    `*hash*`, `*key*`, `ssn`, `cvv`, …, camelCase-aware) can never enter
    `allowed_fields`; a field once in `denied_fields` can never be promoted.
  - New log/config paths must sit under an allowlisted root and are blocked from
    `/etc/serverlens`, `/etc/shadow`, `/etc/ssh`, `**/.ssh/**`, `*.key`/`*.pem`,
    `/root`, `/proc`, `/sys`, … — no path traversal.
  - No `allowed_fields: ['*']`; `max_rows` capped.
- **Auditable & reversible.** Every action is logged to `admin-audit.log`; every
  apply backs up the previous config and rolls back automatically if the merged
  file fails `validate-config`.
- **Reload without downtime.** `apply` sends `SIGHUP` (via `systemctl reload`);
  the service re-reads config and re-registers modules. stdio sessions are
  short-lived and pick up the new config on the next connect regardless.

## Why prompt injection can't widen the whitelist

The write path lives in a **different process**, under a **different identity**,
behind a **factor the model cannot see**. Even if a planted log line convinces the
LLM to "fix access," the most it can do is call `config_suggest` (read-only) and
emit a patch. Nothing changes until a human reviews the diff and types a TOTP code.

## Escape hatch

Anything the policy forbids (exposing a genuinely-needed sensitive-looking column,
rotating tokens, changing the bind host) remains a deliberate, root-only, local
edit of `config.yaml`. That is intentional: those decisions should be rare,
manual and auditable — not reachable through any remote channel.

## Consequences

- New module `serverlens/module/config_suggest.py` (data-plane).
- New package `serverlens/admin/` + `serverlens-admin` entry point (control-plane).
- systemd unit gains `ExecReload`; `Application` gains `reload()` on `SIGHUP`.
- Pure logic (policy, patch validate/merge, TOTP) is unit-tested without root or a DB.

## Status / TODO for production

- `sudoers` snippet to let the ops group run only `serverlens-admin` (documented, not auto-installed).
- Consider rate-limiting / lockout on repeated bad TOTP in `apply`.
- Optional: signed proposals so `propose` (any group member) and `apply` (approver) can be different people.
