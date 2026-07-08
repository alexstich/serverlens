"""ServerLens admin (control-plane).

Privileged, out-of-band configuration management for ServerLens.

This package is deliberately SEPARATE from the read-only MCP service:

  * The MCP service (``serverlens serve``) runs as the unprivileged
    ``serverlens`` user, is sandboxed by systemd and CANNOT write its own
    config. It only ever READS the whitelist in ``/etc/serverlens/config.yaml``.

  * ``serverlens-admin`` is the ONLY component allowed to mutate that
    whitelist. It is meant to be run by a human over SSH (via sudo), guarded
    by a second factor (TOTP) that never travels through the LLM data-plane.

The split exists so that prompt-injection through logs/DB rows can never widen
the whitelist: the write path lives in a different process, under a different
identity, behind a factor the model cannot see.
"""
