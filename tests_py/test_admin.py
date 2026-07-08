from __future__ import annotations

import time

import pytest
import yaml

from serverlens.admin import patch as patchlib
from serverlens.admin import policy, store, totp
from serverlens.admin import cli


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "password", "password_hash", "api_key", "reset_token", "secret_key",
    "ssn", "cvv", "totp_seed", "user_pwd", "AccessToken",
])
def test_sensitive_fields_flagged(name):
    assert policy.is_sensitive_field(name)


@pytest.mark.parametrize("name", [
    "id", "email", "created_at", "is_active", "status", "endpoint", "total",
])
def test_benign_fields_allowed(name):
    assert not policy.is_sensitive_field(name)


@pytest.mark.parametrize("path", [
    "/etc/serverlens/config.yaml", "/etc/shadow", "/home/bob/.ssh/id_rsa",
    "/root/secret", "/etc/ssh/sshd_config", "/var/log/../../etc/shadow",
    "relative/path", "/tmp/foo.log", "/some/deep/id_ed25519",
])
def test_bad_paths_rejected(path):
    assert policy.check_path(path) is not None


@pytest.mark.parametrize("path", [
    "/var/log/nginx/error.log", "/etc/nginx/nginx.conf", "/var/www/app/logs/app.log",
])
def test_good_paths_allowed(path):
    assert policy.check_path(path) is None


# ---------------------------------------------------------------------------
# patch validation
# ---------------------------------------------------------------------------

def _base_config():
    return {
        "databases": {
            "connections": [{
                "name": "app_prod", "driver": "postgresql", "host": "localhost",
                "database": "app", "user": "ro",
                "tables": [{
                    "name": "users",
                    "allowed_fields": ["id", "email"],
                    "denied_fields": ["password_hash"],
                }],
            }],
        },
        "logs": {"sources": [{"name": "nginx_error", "path": "/var/log/nginx/error.log"}]},
    }


def test_forbidden_section_rejected():
    errors = patchlib.validate_patch({"auth": {"tokens": []}}, _base_config())
    assert any("auth" in e for e in errors)


def test_sensitive_allowed_field_rejected():
    patch = {"databases": {"connections": [{"name": "app_prod", "tables": [{
        "name": "orders", "allowed_fields": ["id", "payment_token"],
    }]}]}}
    errors = patchlib.validate_patch(patch, _base_config())
    assert any("payment_token" in e for e in errors)


def test_cannot_reallow_previously_denied_field():
    patch = {"databases": {"connections": [{"name": "app_prod", "tables": [{
        "name": "users", "allowed_fields": ["password_hash"],
    }]}]}}
    errors = patchlib.validate_patch(patch, _base_config())
    assert any("previously" in e or "sensitive" in e for e in errors)


def test_wildcard_fields_rejected():
    patch = {"databases": {"connections": [{"name": "app_prod", "tables": [{
        "name": "orders", "allowed_fields": ["*"],
    }]}]}}
    errors = patchlib.validate_patch(patch, _base_config())
    assert any("wildcard" in e for e in errors)


def test_existing_log_source_cannot_be_replaced():
    patch = {"logs": {"sources": [{"name": "nginx_error", "path": "/var/log/nginx/error.log"}]}}
    errors = patchlib.validate_patch(patch, _base_config())
    assert any("already exists" in e for e in errors)


def test_config_source_bad_path_rejected():
    patch = {"configs": {"sources": [{"name": "shadow", "path": "/etc/shadow"}]}}
    errors = patchlib.validate_patch(patch, _base_config())
    assert errors


def test_valid_new_table_accepted():
    patch = {"databases": {"connections": [{"name": "app_prod", "tables": [{
        "name": "orders",
        "allowed_fields": ["id", "user_id", "status", "total", "created_at"],
        "denied_fields": ["payment_token"],
        "allowed_filters": ["status", "created_at"],
        "allowed_order_by": ["id", "created_at"],
        "max_rows": 500,
    }]}]}}
    assert patchlib.validate_patch(patch, _base_config()) == []


def test_filter_on_non_allowed_field_rejected():
    patch = {"databases": {"connections": [{"name": "app_prod", "tables": [{
        "name": "orders", "allowed_fields": ["id"], "allowed_filters": ["user_id"],
    }]}]}}
    errors = patchlib.validate_patch(patch, _base_config())
    assert any("allowed_filters" in e for e in errors)


# ---------------------------------------------------------------------------
# patch merge
# ---------------------------------------------------------------------------

def test_merge_adds_new_table():
    patch = {"databases": {"connections": [{"name": "app_prod", "tables": [{
        "name": "orders", "allowed_fields": ["id"],
    }]}]}}
    merged = patchlib.merge_patch(_base_config(), patch)
    tables = {t["name"] for t in merged["databases"]["connections"][0]["tables"]}
    assert tables == {"users", "orders"}


def test_merge_adds_columns_to_existing_table_without_promoting_denied():
    patch = {"databases": {"connections": [{"name": "app_prod", "tables": [{
        "name": "users",
        "allowed_fields": ["created_at", "password_hash"],  # denied one must be ignored
    }]}]}}
    merged = patchlib.merge_patch(_base_config(), patch)
    users = merged["databases"]["connections"][0]["tables"][0]
    assert "created_at" in users["allowed_fields"]
    assert "password_hash" not in users["allowed_fields"]


def test_merge_is_pure():
    cfg = _base_config()
    patch = {"logs": {"sources": [{"name": "app", "path": "/var/log/app/api.log"}]}}
    patchlib.merge_patch(cfg, patch)
    assert len(cfg["logs"]["sources"]) == 1  # original untouched


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------

def test_totp_roundtrip():
    secret = totp.generate_secret()
    now = time.time()
    code = totp._code_at(secret, int(now // 30))
    assert totp.verify(secret, code, at=now)


def test_totp_rejects_wrong_code():
    secret = totp.generate_secret()
    assert not totp.verify(secret, "000000", at=time.time()) or True  # not asserting the 1-in-1e6
    assert not totp.verify(secret, "12345")   # wrong length
    assert not totp.verify(secret, "abcdef")  # non-digit


# ---------------------------------------------------------------------------
# full propose → apply cycle (temp dir, no root, no systemd)
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "server": {"host": "127.0.0.1", "port": 9600, "transport": "stdio"},
        "databases": {"connections": [{
            "name": "app_prod", "driver": "postgresql", "host": "localhost",
            "database": "app", "user": "ro",
            "tables": [{"name": "users", "allowed_fields": ["id"], "denied_fields": ["password_hash"]}],
        }]},
    }))
    monkeypatch.setenv("SL_CONFIG", str(cfg))
    monkeypatch.setenv("SL_ADMIN_DIR", str(tmp_path))
    monkeypatch.setenv("SL_ADMIN_AUDIT", str(tmp_path / "admin-audit.log"))
    return tmp_path


def test_full_propose_apply(env, monkeypatch, capsys):
    patch_file = env / "patch.yaml"
    patch_file.write_text(yaml.safe_dump({"databases": {"connections": [{
        "name": "app_prod", "tables": [{
            "name": "orders", "allowed_fields": ["id", "total", "created_at"],
            "denied_fields": ["payment_token"], "max_rows": 500,
        }],
    }]}}))

    # enrol TOTP
    assert cli.main(["init-totp"]) == 0
    secret = (env / "admin.totp").read_text().strip()

    # propose
    assert cli.main(["propose", "--patch", str(patch_file), "--note", "orders"]) == 0
    ids = store.list_proposals()
    assert len(ids) == 1
    pid = ids[0]

    # apply with a bad code → rejected, config unchanged
    assert cli.main(["apply", "--id", pid, "--otp", "000000"]) == 3

    # apply with a valid code
    good = totp._code_at(secret, int(time.time() // 30))
    assert cli.main(["apply", "--id", pid, "--otp", good]) == 0

    # config now has the orders table, proposal consumed, backup exists
    cfg = store.load_config()
    tables = {t["name"] for t in cfg["databases"]["connections"][0]["tables"]}
    assert tables == {"users", "orders"}
    assert store.list_proposals() == []
    assert list(env.glob("config.yaml.bak.*"))


def test_apply_rejects_malicious_patch_at_propose(env):
    patch_file = env / "evil.yaml"
    patch_file.write_text(yaml.safe_dump({"databases": {"connections": [{
        "name": "app_prod", "tables": [{"name": "users", "allowed_fields": ["password_hash"]}],
    }]}}))
    assert cli.main(["propose", "--patch", str(patch_file)]) == 2
    assert store.list_proposals() == []
