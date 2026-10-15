from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import time
import zipfile
from datetime import datetime
from typing import Any, Iterator

from serverlens.config import Config
from serverlens.mcp.tool import Tool
from serverlens.module.base import ModuleInterface, ToolResult
from serverlens.security.redactor import Redactor

# Never served, whatever the source config says: private keys and env files.
_ALWAYS_EXCLUDE: tuple[str, ...] = (
    "*.key", "*.pem", "*.p12", "*.pfx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".env", ".env.*", ".ssh",
)

_DEFAULT_MAX_READ_BYTES = 1024 * 1024
_DEFAULT_MAX_XLSX_BYTES = 50 * 1024 * 1024
_MAX_XLSX_UNCOMPRESSED = 500 * 1024 * 1024
_MAX_LIST_LIMIT = 1000
_MAX_RECURSIVE_SCAN = 100_000
_MAX_READ_LINES = 2000
_MAX_SEARCH_LINES = 1000
_MAX_XLSX_CELLS = 10_000
_SEARCH_TIMEOUT = 5.0
_SNIFF_BYTES = 8192

_CELL_RE = re.compile(r"^([A-Za-z]{1,3})(\d{1,7})$")


class FileReader(ModuleInterface):
    """Read-only access to whitelisted directory trees.

    A source is a root directory. Every request names a path *relative* to that
    root; the resolved real path (symlinks followed) must stay inside the root,
    and neither it nor any of its parents may match an exclude pattern.
    """

    def __init__(self, config: Config) -> None:
        self._sources: dict[str, dict[str, Any]] = {}
        self._redactor = Redactor()

        for source in config.get_file_sources():
            root = source.get("path", "")
            self._sources[source["name"]] = {
                "root": os.path.realpath(root) if root else "",
                "exclude": list(source.get("exclude", []) or []),
                "redact": source.get("redact", []) or [],
                "max_read_bytes": int(source.get("max_read_bytes", _DEFAULT_MAX_READ_BYTES)),
                "max_xlsx_bytes": int(source.get("max_xlsx_bytes", _DEFAULT_MAX_XLSX_BYTES)),
            }

    def get_tools(self) -> list[Tool]:
        source_prop = {"type": "string", "description": "File source name"}
        path_prop = {"type": "string", "description": "Path relative to the source root"}
        return [
            Tool("files_sources", "List available file sources (whitelisted directory trees)", {
                "type": "object", "properties": {},
            }),
            Tool("files_list", "List a directory inside a file source", {
                "type": "object",
                "properties": {
                    "source": source_prop,
                    "path": {**path_prop, "default": ""},
                    "pattern": {"type": "string", "description": "Glob on the entry name, e.g. \"*.xlsx\"", "default": "*"},
                    "recursive": {"type": "boolean", "description": f"Walk subdirectories (scan capped at {_MAX_RECURSIVE_SCAN} entries)", "default": False},
                    "sort": {"type": "string", "enum": ["name", "modified", "size"], "description": "Sort key; modified and size sort descending", "default": "name"},
                    "limit": {"type": "integer", "description": f"Max entries (max {_MAX_LIST_LIMIT})", "default": 200},
                    "offset": {"type": "integer", "description": "Offset for pagination", "default": 0},
                },
                "required": ["source"],
            }),
            Tool("files_stat", "File metadata: size, mtime, detected kind, text encoding, sha256; sheet list for xlsx", {
                "type": "object",
                "properties": {"source": source_prop, "path": path_prop},
                "required": ["source", "path"],
            }),
            Tool("files_read", "Read lines of a text file (utf-8 or cp1251 auto-detected)", {
                "type": "object",
                "properties": {
                    "source": source_prop,
                    "path": path_prop,
                    "offset": {"type": "integer", "description": "First line, 1-based", "default": 1},
                    "lines": {"type": "integer", "description": f"Number of lines (max {_MAX_READ_LINES})", "default": 200},
                },
                "required": ["source", "path"],
            }),
            Tool("files_search", "Search a text file by substring or regex", {
                "type": "object",
                "properties": {
                    "source": source_prop,
                    "path": path_prop,
                    "query": {"type": "string", "description": "Search query"},
                    "regex": {"type": "boolean", "description": "Use regex", "default": False},
                    "lines": {"type": "integer", "description": f"Max matching lines (max {_MAX_SEARCH_LINES})", "default": 200},
                },
                "required": ["source", "path", "query"],
            }),
            Tool("files_xlsx", "Read an .xlsx workbook: without sheet — list sheets; with sheet — cells of a range as TSV", {
                "type": "object",
                "properties": {
                    "source": source_prop,
                    "path": path_prop,
                    "sheet": {"type": "string", "description": "Sheet name or 1-based index; omit to list sheets"},
                    "range": {"type": "string", "description": f"Cell range like \"A1:F50\" (max {_MAX_XLSX_CELLS} cells)", "default": "A1:Z50"},
                },
                "required": ["source", "path"],
            }),
        ]

    def handle_tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        dispatch = {
            "files_sources": self._list_sources,
            "files_list": self._list,
            "files_stat": self._stat,
            "files_read": self._read,
            "files_search": self._search,
            "files_xlsx": self._xlsx,
        }
        handler = dispatch.get(name)
        if handler is None:
            return self.error(f"Unknown tool: {name}")
        return handler(arguments)

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve(self, args: dict[str, Any]) -> tuple[dict[str, Any], str, str] | str:
        """Return (source info, real path, relative path) or an error string."""
        source_name = args.get("source", "")
        info = self._sources.get(source_name)
        if info is None:
            return f"Unknown file source: {source_name}"

        root = info["root"]
        if not root or not os.path.isdir(root):
            return f"File source not available: {source_name}"

        rel = str(args.get("path", "") or "").strip()
        if rel.startswith("/") or "\x00" in rel:
            return "Path must be relative to the source root"
        rel = rel.strip("/")

        real = os.path.realpath(os.path.join(root, rel))
        if real != root and not real.startswith(root + os.sep):
            return "Path is outside the source root"

        rel_real = os.path.relpath(real, root)
        rel_real = "" if rel_real == "." else rel_real
        if self._is_excluded(rel_real, info["exclude"]):
            return "Path is excluded by the source config"
        if not os.path.exists(real):
            return f"Not found: {rel}"

        return info, real, rel_real

    @staticmethod
    def _is_excluded(rel: str, patterns: list[str]) -> bool:
        if not rel:
            return False
        parts = rel.split(os.sep)
        prefixes = [os.sep.join(parts[: i + 1]) for i in range(len(parts))]
        for pattern in list(_ALWAYS_EXCLUDE) + patterns:
            pattern = pattern.strip("/")
            for prefix, part in zip(prefixes, parts):
                if fnmatch.fnmatchcase(prefix, pattern) or fnmatch.fnmatchcase(part, pattern):
                    return True
        return False

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _list_sources(self, _args: dict[str, Any]) -> ToolResult:
        result = []
        for name, info in self._sources.items():
            result.append({
                "name": name,
                "exclude": info["exclude"],
                "available": bool(info["root"]) and os.path.isdir(info["root"]),
            })
        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    def _list(self, args: dict[str, Any]) -> ToolResult:
        resolved = self._resolve(args)
        if isinstance(resolved, str):
            return self.error(resolved)
        info, real, rel = resolved
        if not os.path.isdir(real):
            return self.error(f"Not a directory: {rel}")

        pattern = str(args.get("pattern", "*") or "*")
        recursive = bool(args.get("recursive", False))
        sort = args.get("sort", "name")
        limit = max(1, min(int(args.get("limit", 200)), _MAX_LIST_LIMIT))
        offset = max(0, int(args.get("offset", 0)))

        entries: list[dict[str, Any]] = []
        scanned = 0
        scan_truncated = False
        for entry_rel, st in self._walk(real, info["root"], info["exclude"], recursive):
            scanned += 1
            if scanned > _MAX_RECURSIVE_SCAN:
                scan_truncated = True
                break
            if not fnmatch.fnmatch(os.path.basename(entry_rel), pattern):
                continue
            is_dir = stat.S_ISDIR(st.st_mode)
            entries.append({
                "path": entry_rel + ("/" if is_dir else ""),
                "type": "directory" if is_dir else "file",
                "size": None if is_dir else st.st_size,
                "modified": _fmt_time(st.st_mtime),
                "_mtime": st.st_mtime,
            })

        if sort == "modified":
            entries.sort(key=lambda e: e["_mtime"], reverse=True)
        elif sort == "size":
            entries.sort(key=lambda e: e["size"] or 0, reverse=True)
        else:
            entries.sort(key=lambda e: e["path"])

        page = entries[offset: offset + limit]
        for e in page:
            del e["_mtime"]

        result: dict[str, Any] = {
            "path": rel + "/" if rel else "",
            "total": len(entries),
            "offset": offset,
            "entries": page,
        }
        if scan_truncated:
            result["warning"] = f"Scan stopped after {_MAX_RECURSIVE_SCAN} entries — narrow the path"
        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    def _walk(
        self, start: str, root: str, exclude: list[str], recursive: bool
    ) -> Iterator[tuple[str, os.stat_result]]:
        stack = [start]
        while stack:
            current = stack.pop()
            try:
                it = os.scandir(current)
            except OSError:
                continue
            with it:
                for entry in it:
                    entry_rel = os.path.relpath(entry.path, root)
                    if self._is_excluded(entry_rel, exclude):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISLNK(st.st_mode):
                        # Symlinks are listed only if they stay inside the root.
                        target = os.path.realpath(entry.path)
                        if not target.startswith(root + os.sep):
                            continue
                        try:
                            st = os.stat(target)
                        except OSError:
                            continue
                    yield entry_rel, st
                    if recursive and entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)

    def _stat(self, args: dict[str, Any]) -> ToolResult:
        resolved = self._resolve(args)
        if isinstance(resolved, str):
            return self.error(resolved)
        info, real, rel = resolved

        st = os.stat(real)
        if stat.S_ISDIR(st.st_mode):
            return self.ok(json.dumps({
                "path": rel + "/", "type": "directory", "modified": _fmt_time(st.st_mtime),
            }, indent=2, ensure_ascii=False))
        if not os.access(real, os.R_OK):
            return self.error(f"Permission denied: {rel}")

        kind = _detect_kind(real)
        result: dict[str, Any] = {
            "path": rel,
            "type": "file",
            "kind": kind,
            "size": st.st_size,
            "modified": _fmt_time(st.st_mtime),
            "sha256": _sha256(real),
        }
        if kind == "text":
            result["encoding"] = _detect_encoding(real)
        elif kind == "xlsx":
            sheets = self._xlsx_sheets(real, info)
            if isinstance(sheets, str):
                result["xlsx_error"] = sheets
            else:
                result["sheets"] = sheets
        return self.ok(json.dumps(result, indent=2, ensure_ascii=False))

    def _open_text(self, args: dict[str, Any]) -> tuple[dict[str, Any], str, str, str] | str:
        resolved = self._resolve(args)
        if isinstance(resolved, str):
            return resolved
        info, real, rel = resolved
        if not os.path.isfile(real):
            return f"Not a file: {rel}"
        if not os.access(real, os.R_OK):
            return f"Permission denied: {rel}"
        kind = _detect_kind(real)
        if kind != "text":
            hint = " — use files_xlsx" if kind == "xlsx" else ""
            return f"Not a text file ({kind}): {rel}{hint}"
        return info, real, rel, _detect_encoding(real)

    def _read(self, args: dict[str, Any]) -> ToolResult:
        opened = self._open_text(args)
        if isinstance(opened, str):
            return self.error(opened)
        info, real, rel, encoding = opened

        first = max(1, int(args.get("offset", 1)))
        count = max(1, min(int(args.get("lines", 200)), _MAX_READ_LINES))
        budget = info["max_read_bytes"]

        out: list[str] = []
        more = False
        truncated_bytes = False
        with open(real, "r", encoding=encoding, errors="replace", newline="") as f:
            for number, line in enumerate(f, start=1):
                if number < first:
                    continue
                if len(out) >= count:
                    more = True
                    break
                line = line.rstrip("\r\n")
                budget -= len(line.encode("utf-8")) + 1
                if budget < 0:
                    truncated_bytes = True
                    more = True
                    break
                out.append(f"{number}: {line}")

        text = self._redactor.redact("\n".join(out), info["redact"])
        header = f"# {rel} ({encoding}), lines {first}-{first + len(out) - 1}"
        if not out:
            header = f"# {rel} ({encoding}): no lines from {first}"
        if truncated_bytes:
            header += f"; stopped at {info['max_read_bytes']} bytes"
        if more:
            header += f"; more lines follow — next offset {first + len(out)}"
        return self.ok(header + "\n" + text)

    def _search(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query", ""))
        if not query:
            return self.error("Query must not be empty")
        opened = self._open_text(args)
        if isinstance(opened, str):
            return self.error(opened)
        info, real, rel, encoding = opened

        limit = max(1, min(int(args.get("lines", 200)), _MAX_SEARCH_LINES))
        if args.get("regex", False):
            try:
                pattern = re.compile(query)
            except re.error:
                return self.error("Invalid regex pattern")
            match = lambda s: pattern.search(s) is not None  # noqa: E731
        else:
            lowered = query.lower()
            match = lambda s: lowered in s.lower()  # noqa: E731

        matches: list[str] = []
        start = time.monotonic()
        with open(real, "r", encoding=encoding, errors="replace", newline="") as f:
            for number, line in enumerate(f, start=1):
                if time.monotonic() - start > _SEARCH_TIMEOUT:
                    matches.append(f"[TIMEOUT: search exceeded {_SEARCH_TIMEOUT}s limit]")
                    break
                line = line.rstrip("\r\n")
                if match(line):
                    matches.append(f"{number}: {line}")
                    if len(matches) >= limit:
                        break

        if not matches:
            return self.ok(f"No matches found for: {query}")
        return self.ok(self._redactor.redact("\n".join(matches), info["redact"]))

    def _xlsx(self, args: dict[str, Any]) -> ToolResult:
        resolved = self._resolve(args)
        if isinstance(resolved, str):
            return self.error(resolved)
        info, real, rel = resolved
        if not os.path.isfile(real):
            return self.error(f"Not a file: {rel}")
        kind = _detect_kind(real)
        if kind != "xlsx":
            hint = " — use files_read" if kind == "text" else ""
            return self.error(f"Not an xlsx workbook ({kind}): {rel}{hint}")

        sheet_arg = args.get("sheet")
        if sheet_arg is None or sheet_arg == "":
            sheets = self._xlsx_sheets(real, info)
            if isinstance(sheets, str):
                return self.error(sheets)
            return self.ok(json.dumps({"path": rel, "sheets": sheets}, indent=2, ensure_ascii=False))

        bounds = _parse_range(str(args.get("range", "A1:Z50") or "A1:Z50"))
        if isinstance(bounds, str):
            return self.error(bounds)
        min_row, min_col, max_row, max_col = bounds
        if (max_row - min_row + 1) * (max_col - min_col + 1) > _MAX_XLSX_CELLS:
            return self.error(f"Range too large: max {_MAX_XLSX_CELLS} cells")

        wb = self._load_workbook(real, info)
        if isinstance(wb, str):
            return self.error(wb)
        try:
            ws = _pick_sheet(wb, sheet_arg)
            if ws is None:
                return self.error(f"Unknown sheet: {sheet_arg} (sheets: {wb.sheetnames})")
            rows = []
            for row in ws.iter_rows(
                min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col, values_only=True
            ):
                rows.append("\t".join(_cell_text(v) for v in row))
        finally:
            wb.close()

        while rows and not rows[-1].strip("\t"):
            rows.pop()
        header = f"# {rel} [{ws.title}] {_col_letter(min_col)}{min_row}:{_col_letter(max_col)}{max_row}"
        text = self._redactor.redact("\n".join(rows), info["redact"])
        return self.ok(header + "\n" + text)

    # ------------------------------------------------------------------
    # xlsx helpers
    # ------------------------------------------------------------------

    def _load_workbook(self, real: str, info: dict[str, Any]) -> Any:
        size = os.path.getsize(real)
        if size > info["max_xlsx_bytes"]:
            return f"Workbook too large: {size} bytes (max {info['max_xlsx_bytes']})"
        try:
            with zipfile.ZipFile(real) as zf:
                uncompressed = sum(i.file_size for i in zf.infolist())
        except (zipfile.BadZipFile, OSError):
            return "Corrupt xlsx (not a valid zip)"
        if uncompressed > _MAX_XLSX_UNCOMPRESSED:
            return f"Workbook expands to {uncompressed} bytes — refusing (zip bomb guard)"
        try:
            import openpyxl
        except ImportError:
            return "openpyxl is not installed on the server — rerun scripts/update.sh"
        try:
            return openpyxl.load_workbook(real, read_only=True, data_only=True)
        except Exception as e:  # openpyxl raises a zoo of exception types
            return f"Cannot open workbook: {type(e).__name__}: {e}"

    def _xlsx_sheets(self, real: str, info: dict[str, Any]) -> list[dict[str, Any]] | str:
        wb = self._load_workbook(real, info)
        if isinstance(wb, str):
            return wb
        try:
            sheets = []
            for index, ws in enumerate(wb.worksheets, start=1):
                sheets.append({
                    "index": index,
                    "name": ws.title,
                    # From the sheet's <dimension> tag; may be absent or stale.
                    "dimensions": ws.calculate_dimension() if ws.max_row else None,
                    "max_row": ws.max_row,
                    "max_column": ws.max_column,
                })
            return sheets
        except Exception as e:
            return f"Cannot read sheets: {type(e).__name__}: {e}"
        finally:
            wb.close()


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------

def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _sha256(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _detect_kind(path: str) -> str:
    """Detect file kind by content, not by extension (a .xlsx may be a CSV)."""
    try:
        with open(path, "rb") as f:
            head = f.read(_SNIFF_BYTES)
    except OSError:
        return "unreadable"
    if not head:
        return "empty"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
        except (zipfile.BadZipFile, OSError):
            return "zip"
        if "xl/workbook.xml" in names:
            return "xlsx"
        if "word/document.xml" in names:
            return "docx"
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole2"  # legacy .xls / .doc
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8")):
        return "image"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if b"\x00" in head:
        return "binary"
    return "text"


def _detect_encoding(path: str) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(64 * 1024)
    except OSError:
        return "utf-8"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        # A multibyte char cut at the sniff boundary is still utf-8.
        if e.start >= len(head) - 3:
            return "utf-8"
    return "cp1251"


def _parse_range(text: str) -> tuple[int, int, int, int] | str:
    parts = text.strip().split(":")
    if len(parts) == 1:
        parts = [parts[0], parts[0]]
    if len(parts) != 2:
        return f"Invalid range: {text}"
    cells = []
    for p in parts:
        m = _CELL_RE.match(p.strip())
        if not m:
            return f"Invalid cell reference: {p}"
        cells.append((int(m.group(2)), _col_index(m.group(1))))
    (r1, c1), (r2, c2) = cells
    if r1 < 1 or r2 < 1:
        return f"Invalid range: {text}"
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def _col_letter(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _pick_sheet(wb: Any, sheet: Any) -> Any:
    if isinstance(sheet, int) or (isinstance(sheet, str) and sheet.isdigit() and sheet not in wb.sheetnames):
        idx = int(sheet)
        return wb.worksheets[idx - 1] if 1 <= idx <= len(wb.worksheets) else None
    return wb[sheet] if sheet in wb.sheetnames else None


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    text = str(value)
    return text.replace("\t", " ").replace("\r\n", "\\n").replace("\n", "\\n")
