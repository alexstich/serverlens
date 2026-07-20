from __future__ import annotations

import pytest

from serverlens.config import Config
from serverlens.module.journal_reader import JournalReader

import json


ALLOWED_UNITS = ["nginx", "postgresql", "php8.2-fpm"]


class RecordingExecutor:
    def __init__(self, output: str | None = "") -> None:
        self.output = output
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> str | None:
        self.commands.append(command)
        return self.output


def _make_config(enabled: bool = True) -> Config:
    return Config({
        "server": {"host": "127.0.0.1", "transport": "stdio"},
        "journal": {"enabled": enabled, "allowed_units": ALLOWED_UNITS},
    })


def _make_reader(enabled: bool = True, output: str | None = "") -> tuple[JournalReader, RecordingExecutor]:
    executor = RecordingExecutor(output)
    return JournalReader(_make_config(enabled), executor), executor


# ---------------------------------------------------------------------------
# journal_units / tools
# ---------------------------------------------------------------------------

def test_journal_units_lists_whitelist():
    reader, executor = _make_reader()
    result = reader.handle_tool_call("journal_units", {})
    data = json.loads(result["content"][0]["text"])

    assert data["allowed_units"] == ALLOWED_UNITS
    assert executor.commands == []


def test_tools_exposed_when_enabled():
    reader, _ = _make_reader()
    names = [t.name for t in reader.get_tools()]
    assert names == ["journal_units", "journal_tail", "journal_search"]


def test_disabled_module_has_no_tools_and_rejects_calls():
    reader, executor = _make_reader(enabled=False)

    assert reader.get_tools() == []

    result = reader.handle_tool_call("journal_tail", {"unit": "nginx"})
    assert result["isError"]
    assert executor.commands == []


# ---------------------------------------------------------------------------
# journal_tail
# ---------------------------------------------------------------------------

def test_tail_rejects_unknown_unit():
    reader, executor = _make_reader()
    result = reader.handle_tool_call("journal_tail", {"unit": "sshd"})

    assert result["isError"]
    assert "not in whitelist" in result["content"][0]["text"]
    assert executor.commands == []


@pytest.mark.parametrize("unit", ["ngin", "nginx2", "nginx.service", " nginx", ""])
def test_tail_rejects_partial_unit_match(unit):
    reader, executor = _make_reader()
    result = reader.handle_tool_call("journal_tail", {"unit": unit})

    assert result["isError"]
    assert executor.commands == []


def test_tail_builds_exact_command():
    reader, executor = _make_reader(output="2026-07-18T10:00:01+0300 host nginx[1]: started\n")

    result = reader.handle_tool_call("journal_tail", {"unit": "nginx", "lines": 5})

    assert "isError" not in result
    assert executor.commands == [
        ["journalctl", "-u", "nginx", "-n", "5", "--no-pager", "-o", "short-iso"],
    ]
    assert "started" in result["content"][0]["text"]


def test_tail_caps_lines_at_max():
    reader, executor = _make_reader(output="line\n")

    reader.handle_tool_call("journal_tail", {"unit": "nginx", "lines": 99999})

    assert "500" in executor.commands[0]


def test_tail_failure_returns_error():
    reader, _ = _make_reader(output=None)
    result = reader.handle_tool_call("journal_tail", {"unit": "nginx"})
    assert result["isError"]


def test_tail_empty_journal():
    reader, _ = _make_reader(output="")
    result = reader.handle_tool_call("journal_tail", {"unit": "nginx"})
    assert "Journal is empty" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# journal_search
# ---------------------------------------------------------------------------

def test_search_rejects_unknown_unit():
    reader, executor = _make_reader()
    result = reader.handle_tool_call("journal_search", {"unit": "sshd", "query": "error"})

    assert result["isError"]
    assert executor.commands == []


def test_search_empty_query_returns_error():
    reader, executor = _make_reader()
    result = reader.handle_tool_call("journal_search", {"unit": "nginx", "query": ""})

    assert result["isError"]
    assert executor.commands == []


def test_search_filters_by_substring():
    output = "\n".join([
        "2026-07-18T10:00:01+0300 host nginx[1]: GET /index 200",
        "2026-07-18T10:00:02+0300 host nginx[1]: ERROR upstream timed out",
        "2026-07-18T10:00:03+0300 host nginx[1]: GET /health 200",
        "2026-07-18T10:00:04+0300 host nginx[1]: ERROR connection refused",
    ])
    reader, _ = _make_reader(output=output)

    result = reader.handle_tool_call("journal_search", {"unit": "nginx", "query": "ERROR"})
    lines = result["content"][0]["text"].split("\n")

    assert len(lines) == 2
    assert "upstream timed out" in lines[0]
    assert "connection refused" in lines[1]


def test_search_filters_by_regex():
    output = "\n".join([
        "status 200 ok",
        "status 404 not found",
        "status 502 bad gateway",
    ])
    reader, _ = _make_reader(output=output)

    result = reader.handle_tool_call("journal_search", {
        "unit": "nginx", "query": r"status (4|5)\d{2}", "regex": True,
    })

    assert len(result["content"][0]["text"].split("\n")) == 2


def test_search_invalid_regex_returns_error():
    reader, executor = _make_reader()
    result = reader.handle_tool_call("journal_search", {
        "unit": "nginx", "query": "([unclosed", "regex": True,
    })

    assert result["isError"]
    assert "Invalid regex" in result["content"][0]["text"]
    assert executor.commands == []


def test_search_limits_matches():
    reader, _ = _make_reader(output="\n".join(["ERROR repeated line"] * 50))

    result = reader.handle_tool_call("journal_search", {
        "unit": "nginx", "query": "ERROR", "lines": 10,
    })

    assert len(result["content"][0]["text"].split("\n")) == 10


def test_search_query_never_reaches_shell():
    reader, executor = _make_reader(output="nothing here\n")
    query = "$(reboot); `rm -rf /`"

    result = reader.handle_tool_call("journal_search", {"unit": "nginx", "query": query})

    assert "isError" not in result
    assert len(executor.commands) == 1
    assert all("reboot" not in arg and "rm -rf" not in arg for arg in executor.commands[0])


def test_search_passes_since_until_as_list_args():
    reader, executor = _make_reader(output="")

    reader.handle_tool_call("journal_search", {
        "unit": "nginx",
        "query": "ERROR",
        "since": "3 days ago",
        "until": "2026-07-18 10:00:00",
    })

    command = executor.commands[0]
    assert command[:2] == ["journalctl", "-u"]
    assert ["--since", "3 days ago"] == command[command.index("--since"):command.index("--since") + 2]
    assert ["--until", "2026-07-18 10:00:00"] == command[command.index("--until"):command.index("--until") + 2]


@pytest.mark.parametrize("since", ["$(reboot)", "now; rm -rf /", "a" * 65])
def test_search_rejects_malformed_time_spec(since):
    reader, executor = _make_reader()
    result = reader.handle_tool_call("journal_search", {
        "unit": "nginx", "query": "ERROR", "since": since,
    })

    assert result["isError"]
    assert executor.commands == []


def test_search_no_matches():
    reader, _ = _make_reader(output="some journal line\n")
    result = reader.handle_tool_call("journal_search", {
        "unit": "nginx", "query": "NONEXISTENT_XYZ",
    })

    assert "No matches" in result["content"][0]["text"]
