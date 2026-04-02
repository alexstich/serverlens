from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import yaml


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args else "serve"

    config_path = None
    transport_override = None
    token_arg = None

    i = 1
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            config_path = args[i + 1]
            i += 2
        elif args[i] == "--stdio":
            transport_override = "stdio"
            i += 1
        elif args[i] == "--token" and i + 1 < len(args):
            token_arg = args[i + 1]
            i += 2
        else:
            i += 1

    default_paths = [
        "/etc/serverlens/config.yaml",
        os.path.join(os.path.dirname(__file__), "..", "config.yaml"),
        os.path.join(os.path.dirname(__file__), "..", "config.example.yaml"),
    ]

    if config_path is None:
        for path in default_paths:
            if os.path.isfile(path):
                config_path = path
                break

    if command in ("--help", "-h", "help"):
        _print_usage()
        sys.exit(0)
    elif command == "serve":
        _cmd_serve(config_path, transport_override)
    elif command == "token":
        subcmd = args[1] if len(args) > 1 else "generate"
        _cmd_token(subcmd, args)
    elif command == "validate-config":
        _cmd_validate(config_path)
    else:
        _print_usage()
        sys.exit(1)


def _cmd_serve(config_path: str | None, transport_override: str | None) -> None:
    if config_path is None:
        print("Error: No config file found. Use --config <path>", file=sys.stderr)
        sys.exit(1)

    tmp_config = None
    try:
        if transport_override:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            data.setdefault("server", {})["transport"] = transport_override
            fd, tmp_config = tempfile.mkstemp(prefix="sl_", suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            config_path = tmp_config

        from serverlens.application import Application

        app = Application(config_path)
        app.run()
    except Exception as e:
        print(f"Fatal: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if tmp_config and os.path.isfile(tmp_config):
            os.unlink(tmp_config)


def _cmd_token(subcmd: str, args: list[str]) -> None:
    from serverlens.auth.token_auth import TokenAuth

    if subcmd == "generate":
        token = TokenAuth.generate_token()
        token_hash = TokenAuth.hash_token(token)
        created = datetime.now().strftime("%Y-%m-%d")
        expires = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")

        print("=== New ServerLens Token ===")
        print(f"Token:   {token}")
        print(f"Created: {created}")
        print(f"Expires: {expires}")
        print()
        print("Add this to your config.yaml under auth.tokens:")
        print(f'  - hash: "{token_hash}"')
        print(f'    created: "{created}"')
        print(f'    expires: "{expires}"')
        print()
        print("IMPORTANT: Save the token — it cannot be recovered!")

    elif subcmd == "hash":
        input_token = args[2] if len(args) > 2 else None
        if input_token is None:
            print("Usage: serverlens token hash <token>", file=sys.stderr)
            sys.exit(1)
        print(TokenAuth.hash_token(input_token))

    else:
        print(f"Unknown token command: {subcmd}", file=sys.stderr)
        print("Available: generate, hash", file=sys.stderr)
        sys.exit(1)


def _cmd_validate(config_path: str | None) -> None:
    if config_path is None:
        print("Error: No config file found. Use --config <path>", file=sys.stderr)
        sys.exit(1)
    try:
        from serverlens.config import Config

        Config.load(config_path)
        print(f"Configuration is valid: {config_path}")
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)


def _print_usage() -> None:
    print("""ServerLens — Secure Read-Only Server Diagnostics MCP Server

Usage:
  serverlens serve [--config <path>] [--stdio]    Start the MCP server
  serverlens token generate                        Generate a new auth token
  serverlens token hash <token>                    Hash a token for config
  serverlens validate-config [--config <path>]     Validate config file

Options:
  --config <path>   Path to config.yaml
  --stdio           Force stdio transport (for SSH piping)""")


if __name__ == "__main__":
    main()
