from __future__ import annotations

import re
from typing import Any

_BUILTIN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(password|passwd|pass)\s*[:=]\s*\S+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)(secret|api_key|apikey|api-key)\s*[:=]\s*\S+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)(token|auth_token|access_token)\s*[:=]\s*\S+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)(private_key|private-key)\s*[:=]\s*\S+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)(connection_string|dsn|database_url)\s*[:=]\s*\S+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)(aws_secret|aws_access)\s*[:=]\s*\S+"), r"\1: [REDACTED]"),
]


class Redactor:
    def redact(self, content: str, source_redact: list[Any] | None = None) -> str:
        for pattern, replacement in _BUILTIN_PATTERNS:
            content = pattern.sub(replacement, content)

        for rule in source_redact or []:
            if isinstance(rule, str):
                escaped = re.escape(rule)
                content = re.sub(
                    rf"(?i){escaped}\s*[:=]\s*\S+",
                    f"{rule}: [REDACTED]",
                    content,
                )
            elif isinstance(rule, dict) and "pattern" in rule and "replacement" in rule:
                content = re.sub(rule["pattern"], rule["replacement"], content)

        return content
