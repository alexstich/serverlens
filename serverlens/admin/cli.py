"""``serverlens-admin`` — the privileged, human-driven config editor.

Typical flow (run on the server, over SSH, as an operator with sudo):

    # one-time: enrol a TOTP authenticator
    sudo serverlens-admin init-totp

    # the AI produced a patch (see the config_suggest MCP tool); review + stage it
    sudo serverlens-admin propose --patch new_orders_table.yaml

    # apply it, confirming with a fresh 6-digit code from your authenticator
    sudo serverlens-admin apply --id 20260707-143000-a1b2 --otp 123456

Every apply re-validates the patch, backs up config.yaml, writes atomically,
runs `serverlens validate-config`, then reloads the service.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from serverlens.admin import patch as patchlib
from serverlens.admin import store, totp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="serverlens-admin",
        description="Privileged, audited config editor for ServerLens.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-totp", help="Generate the second-factor TOTP secret")
    p.add_argument("--force", action="store_true", help="Overwrite an existing secret")
    p.add_argument("--account", default=os.getenv("HOSTNAME", "server"))

    p = sub.add_parser("propose", help="Validate and stage a patch for review")
    p.add_argument("--patch", required=True, help="Path to a YAML/JSON patch file")
    p.add_argument("--note", default="", help="Optional note for the audit trail")

    sub.add_parser("list", help="List staged proposals")

    p = sub.add_parser("show", help="Show a staged proposal")
    p.add_argument("--id", required=True)

    p = sub.add_parser("apply", help="Apply a staged proposal (requires TOTP)")
    p.add_argument("--id", required=True)
    p.add_argument("--otp", required=True, help="6-digit code from your authenticator")
    p.add_argument("--no-reload", action="store_true", help="Do not reload the service")

    p = sub.add_parser("discard", help="Delete a staged proposal")
    p.add_argument("--id", required=True)

    args = parser.parse_args(argv)

    handler = {
        "init-totp": _cmd_init_totp,
        "propose": _cmd_propose,
        "list": _cmd_list,
        "show": _cmd_show,
        "apply": _cmd_apply,
        "discard": _cmd_discard,
    }[args.command]
    return handler(args)


# ---------------------------------------------------------------------------

def _cmd_init_totp(args: argparse.Namespace) -> int:
    path = store.totp_secret_path()
    if os.path.isfile(path) and not args.force:
        print(f"TOTP secret already exists at {path} (use --force to replace)", file=sys.stderr)
        return 1

    secret = totp.generate_secret()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secret + "\n")

    print("=== ServerLens admin TOTP enrolled ===")
    print(f"Secret file: {path} (mode 600)")
    print()
    print("Add to your authenticator app (Google Authenticator, 1Password, …):")
    print(f"  Secret: {secret}")
    print(f"  URI:    {totp.provisioning_uri(secret, args.account)}")
    print()
    print("Keep this secret OFF the developer machine and out of the MCP channel.")
    store.audit("init-totp", path)
    return 0


def _cmd_propose(args: argparse.Namespace) -> int:
    try:
        patch = store.load_yaml(args.patch)
    except Exception as e:
        print(f"Cannot read patch: {e}", file=sys.stderr)
        return 1

    try:
        current = store.load_config()
    except Exception as e:
        print(f"Cannot read current config ({store.config_path()}): {e}", file=sys.stderr)
        return 1

    errors = patchlib.validate_patch(patch, current)
    if errors:
        print("Patch REJECTED:", file=sys.stderr)
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        return 2

    summary = patchlib.summarize_patch(current, patch)
    pid = store.new_proposal_id()
    store.save_proposal(pid, {"id": pid, "note": args.note, "patch": patch, "summary": summary})

    print(f"Proposal staged: {pid}")
    print("This change would add:")
    for line in summary:
        print(f"  {line}")
    print()
    print(f"Apply with:  sudo serverlens-admin apply --id {pid} --otp <code>")
    store.audit("propose", f"{pid} :: {'; '.join(summary)}")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    ids = store.list_proposals()
    if not ids:
        print("No staged proposals.")
        return 0
    for pid in ids:
        try:
            rec = store.load_proposal(pid)
            note = rec.get("note", "")
            n = len(rec.get("summary", []))
            print(f"  {pid}  ({n} change(s)){'  — ' + note if note else ''}")
        except Exception:
            print(f"  {pid}  (unreadable)")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        rec = store.load_proposal(args.id)
    except Exception as e:
        print(f"No such proposal: {e}", file=sys.stderr)
        return 1
    import yaml

    print(f"# proposal {args.id}")
    if rec.get("note"):
        print(f"# note: {rec['note']}")
    print("# would add:")
    for line in rec.get("summary", []):
        print(f"#   {line}")
    print()
    print(yaml.safe_dump(rec.get("patch", {}), default_flow_style=False, allow_unicode=True, sort_keys=False))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    # 1. Second factor — before touching anything.
    secret_path = store.totp_secret_path()
    try:
        with open(secret_path, "r", encoding="utf-8") as f:
            secret = f.read().strip()
    except OSError:
        print(f"No TOTP secret at {secret_path} — run `serverlens-admin init-totp` first", file=sys.stderr)
        return 1
    if not totp.verify(secret, args.otp):
        print("Invalid TOTP code.", file=sys.stderr)
        store.audit("apply-denied", f"{args.id} :: bad otp")
        return 3

    # 2. Reload + re-validate the staged patch against the CURRENT config.
    try:
        rec = store.load_proposal(args.id)
    except Exception as e:
        print(f"No such proposal: {e}", file=sys.stderr)
        return 1
    patch = rec.get("patch", {})

    try:
        current = store.load_config()
    except Exception as e:
        print(f"Cannot read current config: {e}", file=sys.stderr)
        return 1

    errors = patchlib.validate_patch(patch, current)
    if errors:
        print("Patch no longer valid against current config:", file=sys.stderr)
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        return 2

    # 3. Merge + write atomically (backup kept).
    merged = patchlib.merge_patch(current, patch)
    try:
        backup = store.write_config_atomic(merged)
    except PermissionError:
        print(f"Permission denied writing {store.config_path()} — run under sudo", file=sys.stderr)
        return 1

    # 4. Validate the file on disk; roll back on failure.
    if not _validate_config_file():
        import shutil
        shutil.copy2(backup, store.config_path())
        print("New config failed validation — rolled back.", file=sys.stderr)
        store.audit("apply-rollback", f"{args.id} :: validation failed, restored {backup}")
        return 4

    store.discard_proposal(args.id)
    store.audit("apply", f"{args.id} :: {'; '.join(rec.get('summary', []))} :: backup={backup}")
    print(f"Applied proposal {args.id}. Backup: {backup}")
    for line in rec.get("summary", []):
        print(f"  {line}")

    # 5. Reload the running service (stdio sessions pick it up on next connect).
    if not args.no_reload:
        _reload_service()
    return 0


def _cmd_discard(args: argparse.Namespace) -> int:
    store.discard_proposal(args.id)
    print(f"Discarded {args.id}")
    store.audit("discard", args.id)
    return 0


# ---------------------------------------------------------------------------

def _validate_config_file() -> bool:
    try:
        from serverlens.config import Config

        Config.load(store.config_path())
        return True
    except Exception as e:
        print(f"  validation error: {e}", file=sys.stderr)
        return False


def _reload_service() -> None:
    if not os.path.exists("/run/systemd/system"):
        print("(no systemd — new config applies on next stdio session)")
        return
    try:
        subprocess.run(
            ["systemctl", "reload-or-restart", "serverlens"],
            check=True, capture_output=True, timeout=15,
        )
        print("Service reloaded.")
    except Exception as e:
        print(f"Config written, but service reload failed: {e}", file=sys.stderr)
        print("Reload manually: sudo systemctl reload-or-restart serverlens", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
