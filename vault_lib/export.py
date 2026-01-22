#!/usr/bin/env python3
"""
Vault Export - Export and system integration utilities.

Provides:
- Export search results to JSON, CSV, or Markdown
- Copy file content to clipboard
- Open files in editor with optional line number
- Query history tracking and retrieval

Database schema for history:
    CREATE TABLE IF NOT EXISTS query_history (
        query_id INTEGER PRIMARY KEY AUTOINCREMENT,
        query_text TEXT NOT NULL,
        executed_at TEXT NOT NULL,
        result_count INTEGER
    )
"""

import csv
import io
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

# Constants
DEFAULT_HISTORY_LIMIT = 20
CLIPBOARD_MAX_CHARS = 1_000_000  # 1MB safety limit


class ExportError(Exception):
    """Base exception for export errors."""
    pass


class ClipboardError(ExportError):
    """Clipboard operation failed."""
    pass


class EditorError(ExportError):
    """Editor launch failed."""
    pass


class FileNotIndexedError(ExportError):
    """File not found in index."""
    pass


@contextmanager
def _get_connection(db_path: Path):
    """Context manager for database connections."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def ensure_history_schema(db_path: Path) -> None:
    """
    Ensure the query_history table exists.

    Args:
        db_path: Path to the SQLite database
    """
    if not db_path.exists():
        return

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS query_history (
                query_id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                result_count INTEGER
            )
        """)
        conn.commit()


def log_query(db_path: Path, query: str, result_count: int) -> None:
    """
    Log a search query to history.

    Called by search.py after executing a search.

    Args:
        db_path: Path to the SQLite database
        query: The search query text
        result_count: Number of results returned
    """
    if not db_path.exists():
        return

    ensure_history_schema(db_path)

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO query_history (query_text, executed_at, result_count) VALUES (?, ?, ?)",
            (query, now, result_count)
        )
        conn.commit()


def _format_json(results: list[dict], pretty: bool = True) -> str:
    """
    Format results as JSON.

    Args:
        results: List of result dictionaries
        pretty: Pretty-print with indentation

    Returns:
        JSON string
    """
    if pretty:
        return json.dumps(results, indent=2, ensure_ascii=False)
    return json.dumps(results, ensure_ascii=False)


def _format_csv(results: list[dict]) -> str:
    """
    Format results as CSV.

    Columns: filepath, file_type, snippet, score

    Args:
        results: List of result dictionaries

    Returns:
        CSV string
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(["filepath", "file_type", "snippet", "score"])

    # Rows
    for r in results:
        writer.writerow([
            r.get("filepath", ""),
            r.get("file_type", ""),
            r.get("snippet", "").replace("\n", " ").strip(),
            r.get("score", 0),
        ])

    return output.getvalue()


def _format_markdown(results: list[dict], query: str = "") -> str:
    """
    Format results as Markdown.

    Args:
        results: List of result dictionaries
        query: Optional query string for header

    Returns:
        Markdown string
    """
    lines = []

    if query:
        lines.append(f"# Search Results: `{query}`\n")
    else:
        lines.append("# Search Results\n")

    lines.append(f"**{len(results)} results found**\n")

    for i, r in enumerate(results, 1):
        filepath = r.get("filepath", "unknown")
        file_type = r.get("file_type", "unknown")
        score = r.get("score", 0)
        snippet = r.get("snippet", "").strip()
        line_num = r.get("line_number", 0)

        lines.append(f"## {i}. `{filepath}`")
        lines.append(f"- **Type:** {file_type}")
        lines.append(f"- **Score:** {score:.4f}")
        if line_num > 0:
            lines.append(f"- **Line:** {line_num}")
        lines.append("")

        if snippet:
            # Determine code fence language
            lang_map = {
                "python": "python",
                "py": "python",
                "javascript": "javascript",
                "js": "javascript",
                "typescript": "typescript",
                "ts": "typescript",
                "markdown": "markdown",
                "md": "markdown",
                "json": "json",
                "yaml": "yaml",
                "yml": "yaml",
                "shell": "bash",
                "sh": "bash",
                "html": "html",
                "css": "css",
            }
            lang = lang_map.get(file_type, "")
            lines.append(f"```{lang}")
            lines.append(snippet)
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


def cmd_export(
    results: list[dict],
    format: str = "json",
    output: Optional[str] = None,
    query: str = "",
) -> dict:
    """
    Export search results in specified format.

    Args:
        results: List of result dictionaries from search
        format: Output format - 'json', 'csv', or 'md' (default: json)
        output: Output file path (default: None = return string)
        query: Optional query string for Markdown header

    Returns:
        Dictionary with:
        - success: bool
        - format: str
        - output_path: str or None
        - content: str (if no output path)
        - char_count: int
        - error: str or None
    """
    format = format.lower()
    if format not in ("json", "csv", "md", "markdown"):
        return {
            "success": False,
            "format": format,
            "output_path": None,
            "content": "",
            "char_count": 0,
            "error": f"Invalid format: {format}. Use 'json', 'csv', or 'md'",
        }

    # Normalize format
    if format == "markdown":
        format = "md"

    try:
        if format == "json":
            content = _format_json(results)
        elif format == "csv":
            content = _format_csv(results)
        else:  # md
            content = _format_markdown(results, query)

        if output:
            output_path = Path(output).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            return {
                "success": True,
                "format": format,
                "output_path": str(output_path),
                "content": "",
                "char_count": len(content),
                "error": None,
            }
        else:
            return {
                "success": True,
                "format": format,
                "output_path": None,
                "content": content,
                "char_count": len(content),
                "error": None,
            }

    except Exception as e:
        return {
            "success": False,
            "format": format,
            "output_path": output,
            "content": "",
            "char_count": 0,
            "error": str(e),
        }


def _copy_to_clipboard_pyperclip(content: str) -> bool:
    """
    Try to copy content using pyperclip.

    Returns True on success, False if pyperclip unavailable.
    """
    try:
        import pyperclip
        pyperclip.copy(content)
        return True
    except ImportError:
        return False
    except Exception:
        return False


def _copy_to_clipboard_xclip(content: str) -> bool:
    """
    Try to copy content using xclip.

    Returns True on success, False on failure.
    """
    # Ensure DISPLAY is set for X11
    env = os.environ.copy()
    if "DISPLAY" not in env:
        env["DISPLAY"] = ":0"

    try:
        proc = subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=content.encode("utf-8"),
            capture_output=True,
            timeout=5,
            env=env,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:
        return False


def _get_file_content_from_db(db_path: Path, filepath: str) -> Optional[str]:
    """
    Look up file content from the database.

    Args:
        db_path: Path to SQLite database
        filepath: File path (relative or absolute)

    Returns:
        File content string, or None if not found
    """
    if not db_path.exists():
        return None

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Try exact match first
        cursor.execute(
            "SELECT content FROM files WHERE file_path = ?",
            (filepath,)
        )
        row = cursor.fetchone()

        if row:
            return row["content"]

        # Try matching just the filename
        filename = Path(filepath).name
        cursor.execute(
            "SELECT content, file_path FROM files WHERE filename = ?",
            (filename,)
        )
        row = cursor.fetchone()

        if row:
            return row["content"]

        # Try partial path match (e.g., user provides relative path)
        cursor.execute(
            "SELECT content FROM files WHERE file_path LIKE ?",
            (f"%{filepath}",)
        )
        row = cursor.fetchone()

        if row:
            return row["content"]

    return None


def cmd_clip(
    filepath: str,
    db_path: Path,
) -> dict:
    """
    Copy file content to clipboard.

    Args:
        filepath: File path (from index or filesystem)
        db_path: Path to SQLite database

    Returns:
        Dictionary with:
        - success: bool
        - filepath: str
        - char_count: int
        - method: str ('pyperclip', 'xclip', or None)
        - error: str or None
    """
    # First, try to get content from database
    content = _get_file_content_from_db(db_path, filepath)

    # If not in DB, try reading from filesystem
    if content is None:
        file_path = Path(filepath).expanduser().resolve()
        if file_path.exists() and file_path.is_file():
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = file_path.read_text(encoding="latin-1")
                except Exception as e:
                    return {
                        "success": False,
                        "filepath": filepath,
                        "char_count": 0,
                        "method": None,
                        "error": f"Could not read file: {e}",
                    }
            except Exception as e:
                return {
                    "success": False,
                    "filepath": filepath,
                    "char_count": 0,
                    "method": None,
                    "error": f"Could not read file: {e}",
                }
        else:
            return {
                "success": False,
                "filepath": filepath,
                "char_count": 0,
                "method": None,
                "error": f"File not found in index or filesystem: {filepath}",
            }

    # Safety check for clipboard size
    if len(content) > CLIPBOARD_MAX_CHARS:
        return {
            "success": False,
            "filepath": filepath,
            "char_count": len(content),
            "method": None,
            "error": f"File too large for clipboard ({len(content):,} chars > {CLIPBOARD_MAX_CHARS:,} limit)",
        }

    # Try pyperclip first
    if _copy_to_clipboard_pyperclip(content):
        return {
            "success": True,
            "filepath": filepath,
            "char_count": len(content),
            "method": "pyperclip",
            "error": None,
        }

    # Fall back to xclip
    if _copy_to_clipboard_xclip(content):
        return {
            "success": True,
            "filepath": filepath,
            "char_count": len(content),
            "method": "xclip",
            "error": None,
        }

    return {
        "success": False,
        "filepath": filepath,
        "char_count": len(content),
        "method": None,
        "error": "Clipboard copy failed. Install pyperclip or xclip.",
    }


def _get_absolute_path_from_db(db_path: Path, filepath: str, root_path: Optional[Path] = None) -> Optional[Path]:
    """
    Get the absolute path for a file from the database.

    Args:
        db_path: Path to SQLite database
        filepath: File path (relative or partial)
        root_path: Root directory (for resolving relative paths)

    Returns:
        Absolute Path object, or None if not found
    """
    if not db_path.exists():
        return None

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Try exact match
        cursor.execute(
            "SELECT file_path FROM files WHERE file_path = ?",
            (filepath,)
        )
        row = cursor.fetchone()

        if not row:
            # Try partial match
            cursor.execute(
                "SELECT file_path FROM files WHERE file_path LIKE ?",
                (f"%{filepath}%",)
            )
            row = cursor.fetchone()

        if row:
            db_filepath = row["file_path"]
            # Check if it's already absolute
            path = Path(db_filepath)
            if path.is_absolute():
                return path

            # Resolve relative to root_path
            if root_path:
                return (root_path / db_filepath).resolve()

            # Try to guess root from db_path
            # Typically db is at root/.vault_state/vault.db or root/.vault.db
            if db_path.parent.name == ".vault_state":
                return (db_path.parent.parent / db_filepath).resolve()
            else:
                return (db_path.parent / db_filepath).resolve()

    return None


def _get_editor_command(filepath: Path, line: Optional[int] = None) -> list[str]:
    """
    Build editor command with line number support.

    Checks $EDITOR, falls back to common editors.

    Args:
        filepath: Absolute path to file
        line: Optional line number

    Returns:
        Command list for subprocess
    """
    editor = os.environ.get("EDITOR", "").strip()

    # Common editors and their line-number syntax
    editor_line_syntax = {
        "vim": lambda f, n: ["vim", f"+{n}", str(f)] if n else ["vim", str(f)],
        "nvim": lambda f, n: ["nvim", f"+{n}", str(f)] if n else ["nvim", str(f)],
        "nano": lambda f, n: ["nano", f"+{n}", str(f)] if n else ["nano", str(f)],
        "emacs": lambda f, n: ["emacs", f"+{n}", str(f)] if n else ["emacs", str(f)],
        "code": lambda f, n: ["code", "-g", f"{f}:{n}" if n else str(f)],
        "subl": lambda f, n: ["subl", f"{f}:{n}" if n else str(f)],
        "gedit": lambda f, n: ["gedit", f"+{n}", str(f)] if n else ["gedit", str(f)],
        "kate": lambda f, n: ["kate", "-l", str(n), str(f)] if n else ["kate", str(f)],
    }

    if editor:
        # Extract base name for matching
        editor_base = Path(editor).name.lower()

        # Check if it's a known editor
        for name, cmd_builder in editor_line_syntax.items():
            if name in editor_base:
                return cmd_builder(filepath, line)

        # Unknown editor - just use it directly
        if line:
            # Try generic +N syntax
            return [editor, f"+{line}", str(filepath)]
        return [editor, str(filepath)]

    # No $EDITOR set - try to find one
    for editor_name in ["code", "vim", "nvim", "nano", "gedit"]:
        if shutil.which(editor_name):
            return editor_line_syntax[editor_name](filepath, line)

    # Last resort: xdg-open (no line number support)
    return ["xdg-open", str(filepath)]


def cmd_open(
    filepath: str,
    db_path: Path,
    line: Optional[int] = None,
    root_path: Optional[Path] = None,
) -> dict:
    """
    Open file in editor with optional line number.

    Args:
        filepath: File path (from index)
        db_path: Path to SQLite database
        line: Optional line number to jump to
        root_path: Optional root directory for resolving paths

    Returns:
        Dictionary with:
        - success: bool
        - filepath: str (absolute path)
        - editor: str (editor command used)
        - line: int or None
        - error: str or None
    """
    # Try to resolve the absolute path
    abs_path = _get_absolute_path_from_db(db_path, filepath, root_path)

    # If not in DB, check if it's already a valid path
    if abs_path is None:
        test_path = Path(filepath).expanduser().resolve()
        if test_path.exists() and test_path.is_file():
            abs_path = test_path

    if abs_path is None:
        return {
            "success": False,
            "filepath": filepath,
            "editor": None,
            "line": line,
            "error": f"File not found in index or filesystem: {filepath}",
        }

    if not abs_path.exists():
        return {
            "success": False,
            "filepath": str(abs_path),
            "editor": None,
            "line": line,
            "error": f"File does not exist: {abs_path}",
        }

    # Build and execute editor command
    cmd = _get_editor_command(abs_path, line)
    editor_name = cmd[0]

    try:
        # Use Popen to not block on GUI editors
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {
            "success": True,
            "filepath": str(abs_path),
            "editor": editor_name,
            "line": line,
            "error": None,
        }
    except FileNotFoundError:
        # Editor not found, try xdg-open as fallback
        try:
            subprocess.Popen(
                ["xdg-open", str(abs_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "success": True,
                "filepath": str(abs_path),
                "editor": "xdg-open",
                "line": None,  # xdg-open doesn't support line numbers
                "error": None,
            }
        except Exception as e:
            return {
                "success": False,
                "filepath": str(abs_path),
                "editor": None,
                "line": line,
                "error": f"Could not open editor: {e}",
            }
    except Exception as e:
        return {
            "success": False,
            "filepath": str(abs_path),
            "editor": editor_name,
            "line": line,
            "error": f"Editor launch failed: {e}",
        }


def cmd_history(
    db_path: Path,
    limit: int = DEFAULT_HISTORY_LIMIT,
    clear: bool = False,
) -> dict:
    """
    Query or clear search history.

    Args:
        db_path: Path to SQLite database
        limit: Maximum queries to return (default 20)
        clear: If True, clear all history

    Returns:
        Dictionary with:
        - success: bool
        - action: str ('list' or 'clear')
        - count: int (number of queries returned or cleared)
        - queries: list of dicts (if action='list')
        - error: str or None
    """
    if not db_path.exists():
        return {
            "success": False,
            "action": "clear" if clear else "list",
            "count": 0,
            "queries": [],
            "error": "Database not found",
        }

    ensure_history_schema(db_path)

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        if clear:
            cursor.execute("DELETE FROM query_history")
            deleted_count = cursor.rowcount
            conn.commit()
            return {
                "success": True,
                "action": "clear",
                "count": deleted_count,
                "queries": [],
                "error": None,
            }

        # List recent queries
        cursor.execute("""
            SELECT query_id, query_text, executed_at, result_count
            FROM query_history
            ORDER BY executed_at DESC
            LIMIT ?
        """, (limit,))

        queries = []
        for row in cursor.fetchall():
            queries.append({
                "query_id": row["query_id"],
                "query": row["query_text"],
                "executed_at": row["executed_at"],
                "result_count": row["result_count"],
            })

        return {
            "success": True,
            "action": "list",
            "count": len(queries),
            "queries": queries,
            "error": None,
        }
