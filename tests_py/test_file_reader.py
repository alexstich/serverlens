from __future__ import annotations

import json
import os
from pathlib import Path

import openpyxl
import pytest

from serverlens.config import Config
from serverlens.module.file_reader import FileReader


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    base = tmp_path / "runtime"
    (base / "file_import" / "accounts").mkdir(parents=True)
    (base / "dumps").mkdir()
    (base / "dumps" / "db.sql").write_text("INSERT INTO users VALUES ('hash');\n")
    (base / "logs").mkdir()
    (base / "logs" / "app.log").write_text("line one\nERROR two\nline three\napi_key=abc123\n")
    (base / "server.key").write_text("-----BEGIN PRIVATE KEY-----\n")
    (base / "file_import" / "accounts" / "utf8.csv").write_text("Адрес;Площадь\nул. Ленина;42\n", encoding="utf-8")
    (base / "file_import" / "accounts" / "win.csv").write_bytes("Адрес;Площадь\n".encode("cp1251"))
    (base / "binary.bin").write_bytes(b"\x00\x01\x02garbage")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Основные сведения"
    ws.append(["Номер ЛС", "Адрес"])
    ws.append(["001", "ул. Мира, 1"])
    wb.create_sheet("Помещения").append(["Помещение"])
    wb.save(base / "file_import" / "accounts" / "book.xlsx")

    # A CSV that arrived with an .xlsx extension.
    (base / "file_import" / "accounts" / "fake.xlsx").write_text("a;b\n1;2\n")

    outside = tmp_path / "outside.txt"
    outside.write_text("secret outside\n")
    os.symlink(outside, base / "escape.txt")
    return base


def _reader(root: Path, **extra) -> FileReader:
    source = {"name": "runtime", "path": str(root), "exclude": ["dumps"], **extra}
    return FileReader(Config({
        "server": {"host": "127.0.0.1", "transport": "stdio"},
        "files": {"sources": [source]},
    }))


def _call(reader: FileReader, tool: str, **args):
    return reader.handle_tool_call(tool, {"source": "runtime", **args})


def _text(result) -> str:
    return result["content"][0]["text"]


def _json(result):
    assert not result.get("isError"), _text(result)
    return json.loads(_text(result))


# ---------------------------------------------------------------------------
# Path guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "file_import/../../outside.txt"])
def test_paths_outside_root_rejected(root, path):
    result = _call(_reader(root), "files_read", path=path)
    assert result.get("isError")


def test_symlink_escaping_root_rejected(root):
    result = _call(_reader(root), "files_read", path="escape.txt")
    assert result.get("isError")
    assert "outside" in _text(result)


def test_excluded_directory_and_its_children_rejected(root):
    reader = _reader(root)
    assert _call(reader, "files_list", path="dumps").get("isError")
    assert _call(reader, "files_read", path="dumps/db.sql").get("isError")


def test_private_keys_always_excluded(root):
    reader = _reader(root)
    assert _call(reader, "files_read", path="server.key").get("isError")
    names = [e["path"] for e in _json(_call(reader, "files_list"))["entries"]]
    assert "server.key" not in names


def test_unknown_source_rejected(root):
    result = _reader(root).handle_tool_call("files_list", {"source": "nope"})
    assert result.get("isError")


# ---------------------------------------------------------------------------
# files_list
# ---------------------------------------------------------------------------

def test_list_root_hides_excluded_and_escaping_entries(root):
    names = [e["path"] for e in _json(_call(_reader(root), "files_list"))["entries"]]
    assert "file_import/" in names
    assert "logs/" in names
    assert "dumps/" not in names
    assert "escape.txt" not in names


def test_list_recursive_with_pattern(root):
    data = _json(_call(_reader(root), "files_list", path="file_import", pattern="*.xlsx", recursive=True))
    assert [e["path"] for e in data["entries"]] == [
        "file_import/accounts/book.xlsx",
        "file_import/accounts/fake.xlsx",
    ]


def test_list_paginates(root):
    data = _json(_call(_reader(root), "files_list", path="file_import/accounts", limit=2, offset=1))
    assert data["total"] == 4
    assert len(data["entries"]) == 2


# ---------------------------------------------------------------------------
# files_stat / kind detection
# ---------------------------------------------------------------------------

def test_stat_xlsx_lists_sheets(root):
    data = _json(_call(_reader(root), "files_stat", path="file_import/accounts/book.xlsx"))
    assert data["kind"] == "xlsx"
    assert [s["name"] for s in data["sheets"]] == ["Основные сведения", "Помещения"]
    assert len(data["sha256"]) == 64


def test_stat_detects_csv_disguised_as_xlsx(root):
    data = _json(_call(_reader(root), "files_stat", path="file_import/accounts/fake.xlsx"))
    assert data["kind"] == "text"


def test_stat_detects_cp1251(root):
    data = _json(_call(_reader(root), "files_stat", path="file_import/accounts/win.csv"))
    assert data["encoding"] == "cp1251"


# ---------------------------------------------------------------------------
# files_read / files_search
# ---------------------------------------------------------------------------

def test_read_numbers_lines_and_reports_next_offset(root):
    text = _text(_call(_reader(root), "files_read", path="logs/app.log", offset=2, lines=1))
    assert "2: ERROR two" in text
    assert "next offset 3" in text


def test_read_decodes_cp1251(root):
    text = _text(_call(_reader(root), "files_read", path="file_import/accounts/win.csv"))
    assert "1: Адрес;Площадь" in text


def test_read_redacts_secrets(root):
    text = _text(_call(_reader(root), "files_read", path="logs/app.log"))
    assert "abc123" not in text
    assert "[REDACTED]" in text


def test_read_refuses_binary_and_points_xlsx_to_files_xlsx(root):
    reader = _reader(root)
    assert _call(reader, "files_read", path="binary.bin").get("isError")
    result = _call(reader, "files_read", path="file_import/accounts/book.xlsx")
    assert result.get("isError")
    assert "files_xlsx" in _text(result)


def test_read_respects_byte_budget(root):
    text = _text(_call(_reader(root, max_read_bytes=12), "files_read", path="logs/app.log"))
    assert "1: line one" in text
    assert "ERROR two" not in text
    assert "stopped at 12 bytes" in text


def test_search_substring_and_regex(root):
    reader = _reader(root)
    assert _text(_call(reader, "files_search", path="logs/app.log", query="error")) == "2: ERROR two"
    text = _text(_call(reader, "files_search", path="logs/app.log", query=r"^line \w+$", regex=True))
    assert text == "1: line one\n3: line three"


# ---------------------------------------------------------------------------
# files_xlsx
# ---------------------------------------------------------------------------

def test_xlsx_reads_range_by_sheet_name(root):
    text = _text(_call(_reader(root), "files_xlsx", path="file_import/accounts/book.xlsx",
                       sheet="Основные сведения", range="A1:B5"))
    lines = text.split("\n")
    assert lines[0].startswith("# file_import/accounts/book.xlsx [Основные сведения] A1:B5")
    assert lines[1:] == ["Номер ЛС\tАдрес", "001\tул. Мира, 1"]


def test_xlsx_reads_by_index(root):
    text = _text(_call(_reader(root), "files_xlsx", path="file_import/accounts/book.xlsx", sheet="2", range="A1"))
    assert text.split("\n")[1] == "Помещение"


def test_xlsx_rejects_huge_range_and_non_workbook(root):
    reader = _reader(root)
    assert _call(reader, "files_xlsx", path="file_import/accounts/book.xlsx", sheet="1", range="A1:ZZ1000").get("isError")
    result = _call(reader, "files_xlsx", path="file_import/accounts/fake.xlsx")
    assert result.get("isError")
    assert "files_read" in _text(result)


def test_xlsx_rejects_oversized_workbook(root):
    result = _call(_reader(root, max_xlsx_bytes=100), "files_xlsx", path="file_import/accounts/book.xlsx")
    assert result.get("isError")
    assert "too large" in _text(result)
