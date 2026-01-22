#!/usr/bin/env python3
"""
Vault Analysis Module - Codebase analysis and introspection commands.

Provides commands for analyzing indexed files:
- stats: Codebase statistics (file counts, sizes, types)
- timeline: Files by modification/index date
- tags: Extract tags, markers, and frontmatter
- outline: File structure extraction (headers, functions, classes)
- toc: Table of contents for all indexed files

All functions return dictionaries suitable for JSON output.
"""

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


@contextmanager
def _get_connection(db_path: Path):
    """Context manager for database connections."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _check_db_exists(db_path: Path) -> Optional[dict]:
    """
    Check if database exists and has required tables.

    Returns None if OK, or error dict if not.
    """
    if not db_path.exists():
        return {
            "error": "Database not found",
            "db_path": str(db_path),
            "hint": "Run './vault setup' first"
        }

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
            )
            if not cursor.fetchone():
                return {
                    "error": "Database not initialized (files table missing)",
                    "hint": "Run './vault setup' first"
                }
    except sqlite3.Error as e:
        return {"error": f"Database error: {e}"}

    return None


def cmd_stats(db_path: Path) -> dict:
    """
    Get codebase statistics from the indexed database.

    Returns:
        Dictionary containing:
        - total_files: Total number of indexed files
        - total_size_bytes: Sum of all file sizes
        - total_size_human: Human-readable total size
        - total_words: Approximate word count across all files
        - file_types: Breakdown by file type {type: count}
        - largest_files: List of 10 largest files
        - index_age: Time since last index update
        - db_size_bytes: Size of the database file
    """
    error = _check_db_exists(db_path)
    if error:
        return error

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Total files and size
            cursor.execute("""
                SELECT
                    COUNT(*) as total_files,
                    COALESCE(SUM(file_size), 0) as total_size
                FROM files
            """)
            row = cursor.fetchone()
            total_files = row["total_files"]
            total_size = row["total_size"]

            # Word count (approximate: split by whitespace)
            cursor.execute("SELECT content FROM files")
            total_words = 0
            for content_row in cursor.fetchall():
                content = content_row["content"] or ""
                total_words += len(content.split())

            # File type breakdown
            cursor.execute("""
                SELECT file_type, COUNT(*) as count
                FROM files
                GROUP BY file_type
                ORDER BY count DESC
            """)
            file_types = {row["file_type"]: row["count"] for row in cursor.fetchall()}

            # 10 largest files
            cursor.execute("""
                SELECT file_path, filename, file_type, file_size
                FROM files
                ORDER BY file_size DESC
                LIMIT 10
            """)
            largest_files = []
            for row in cursor.fetchall():
                largest_files.append({
                    "filepath": row["file_path"],
                    "filename": row["filename"],
                    "file_type": row["file_type"],
                    "size_bytes": row["file_size"],
                    "size_human": _human_readable_size(row["file_size"]),
                })

            # Index age from manifest
            index_age = None
            last_indexed_at = None
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='manifest'
            """)
            if cursor.fetchone():
                cursor.execute("SELECT last_indexed_at FROM manifest WHERE id = 1")
                manifest_row = cursor.fetchone()
                if manifest_row and manifest_row["last_indexed_at"]:
                    try:
                        last_indexed = datetime.fromisoformat(manifest_row["last_indexed_at"])
                        last_indexed_at = manifest_row["last_indexed_at"]
                        age = datetime.now() - last_indexed
                        index_age = _human_readable_duration(age)
                    except (ValueError, TypeError):
                        pass

            return {
                "total_files": total_files,
                "total_size_bytes": total_size,
                "total_size_human": _human_readable_size(total_size),
                "total_words": total_words,
                "file_types": file_types,
                "largest_files": largest_files,
                "last_indexed_at": last_indexed_at,
                "index_age": index_age,
                "db_size_bytes": db_path.stat().st_size,
                "db_size_human": _human_readable_size(db_path.stat().st_size),
            }

    except sqlite3.Error as e:
        return {"error": f"Database error: {e}"}


def cmd_timeline(db_path: Path, days: int = 30, limit: int = 50) -> dict:
    """
    Get files sorted by index date, grouped by day.

    Args:
        db_path: Path to the SQLite database
        days: Number of days to look back (default 30)
        limit: Maximum files to return (default 50)

    Returns:
        Dictionary containing:
        - days_requested: Number of days requested
        - total_files: Total files in range
        - groups: List of day groups, each with date and files
    """
    error = _check_db_exists(db_path)
    if error:
        return error

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Calculate cutoff date
            cutoff = datetime.now() - timedelta(days=days)
            cutoff_iso = cutoff.isoformat()

            # Get files within date range, ordered by indexed_at descending
            cursor.execute("""
                SELECT file_path, filename, file_type, file_size, indexed_at
                FROM files
                WHERE indexed_at >= ?
                ORDER BY indexed_at DESC
                LIMIT ?
            """, (cutoff_iso, limit))

            rows = cursor.fetchall()

            # Group by day
            groups = {}
            for row in rows:
                indexed_at = row["indexed_at"]
                # Extract date part (YYYY-MM-DD)
                if indexed_at:
                    try:
                        dt = datetime.fromisoformat(indexed_at)
                        date_key = dt.strftime("%Y-%m-%d")
                        weekday = dt.strftime("%A")
                    except ValueError:
                        date_key = "unknown"
                        weekday = "unknown"
                else:
                    date_key = "unknown"
                    weekday = "unknown"

                if date_key not in groups:
                    groups[date_key] = {
                        "date": date_key,
                        "weekday": weekday,
                        "files": []
                    }

                groups[date_key]["files"].append({
                    "filepath": row["file_path"],
                    "filename": row["filename"],
                    "file_type": row["file_type"],
                    "file_size": row["file_size"],
                    "indexed_at": indexed_at,
                })

            # Convert to list sorted by date descending
            sorted_groups = sorted(
                groups.values(),
                key=lambda g: g["date"],
                reverse=True
            )

            return {
                "days_requested": days,
                "total_files": len(rows),
                "groups": sorted_groups,
            }

    except sqlite3.Error as e:
        return {"error": f"Database error: {e}"}


def cmd_tags(db_path: Path) -> dict:
    """
    Extract tags and markers from indexed content.

    Scans for:
    - YAML frontmatter (between --- markers at file start)
    - Hashtags (#word patterns, excluding code comments)
    - TODO/FIXME/HACK/XXX markers with surrounding context

    Returns:
        Dictionary containing:
        - frontmatter_files: List of files with YAML frontmatter
        - hashtags: Dict of {tag: [files containing it]}
        - markers: Dict of {marker_type: [{file, line, context}]}
    """
    error = _check_db_exists(db_path)
    if error:
        return error

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_path, filename, file_type, content FROM files")

            frontmatter_files = []
            hashtags = {}
            markers = {
                "TODO": [],
                "FIXME": [],
                "HACK": [],
                "XXX": [],
                "NOTE": [],
                "BUG": [],
            }

            # Regex patterns
            frontmatter_pattern = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)
            hashtag_pattern = re.compile(r'(?<!\w)#([a-zA-Z][a-zA-Z0-9_-]{1,30})(?!\w)')
            # Marker pattern: requires marker to be followed by : or whitespace and more text
            # This avoids matching "Notes" or "Todos" as markers
            marker_pattern = re.compile(
                r'\b(TODO|FIXME|HACK|XXX|NOTE|BUG)(?::|(?=\s))[\s:]*(.{0,80})',
                re.IGNORECASE
            )

            for row in cursor.fetchall():
                file_path = row["file_path"]
                content = row["content"] or ""

                # Check for YAML frontmatter
                fm_match = frontmatter_pattern.match(content)
                if fm_match:
                    frontmatter_content = fm_match.group(1)
                    # Extract key-value pairs from frontmatter
                    fm_data = {}
                    for line in frontmatter_content.split('\n'):
                        if ':' in line:
                            key, _, value = line.partition(':')
                            fm_data[key.strip()] = value.strip()

                    frontmatter_files.append({
                        "filepath": file_path,
                        "frontmatter": fm_data,
                    })

                # Find hashtags (skip code files to reduce noise)
                if row["file_type"] in ("markdown", "text"):
                    for match in hashtag_pattern.finditer(content):
                        tag = match.group(1).lower()
                        if tag not in hashtags:
                            hashtags[tag] = []
                        if file_path not in hashtags[tag]:
                            hashtags[tag].append(file_path)

                # Find markers (TODO, FIXME, etc.)
                lines = content.split('\n')
                for line_num, line in enumerate(lines, start=1):
                    for match in marker_pattern.finditer(line):
                        marker_type = match.group(1).upper()
                        context = match.group(2).strip()

                        if marker_type in markers:
                            markers[marker_type].append({
                                "filepath": file_path,
                                "line": line_num,
                                "context": context[:100] if context else "",
                            })

            # Sort hashtags by frequency
            sorted_hashtags = dict(
                sorted(hashtags.items(), key=lambda x: -len(x[1]))
            )

            # Count summary
            marker_counts = {k: len(v) for k, v in markers.items() if v}

            return {
                "frontmatter_files": frontmatter_files,
                "frontmatter_count": len(frontmatter_files),
                "hashtags": sorted_hashtags,
                "hashtag_count": len(sorted_hashtags),
                "markers": markers,
                "marker_counts": marker_counts,
            }

    except sqlite3.Error as e:
        return {"error": f"Database error: {e}"}


def cmd_outline(db_path: Path, filepath: str) -> dict:
    """
    Extract structural outline from a file.

    Extracts based on file type:
    - Markdown: headers (# ## ### etc.)
    - Python: class/def signatures
    - JavaScript/TypeScript: function/class/const declarations
    - Generic: numbered sections, indentation-based structure

    Args:
        db_path: Path to the SQLite database
        filepath: Path to the file (exact or partial match)

    Returns:
        Dictionary containing:
        - filepath: Matched file path
        - file_type: Detected file type
        - outline: List of {line, level, type, text}
    """
    error = _check_db_exists(db_path)
    if error:
        return error

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Find the file (exact match first, then partial)
            cursor.execute(
                "SELECT file_path, filename, file_type, content FROM files WHERE file_path = ?",
                (filepath,)
            )
            row = cursor.fetchone()

            if not row:
                cursor.execute(
                    "SELECT file_path, filename, file_type, content FROM files "
                    "WHERE file_path LIKE ? OR filename = ?",
                    (f"%{filepath}%", filepath)
                )
                row = cursor.fetchone()

            if not row:
                return {
                    "error": f"File not found: {filepath}",
                    "hint": "Use './vault list' to see indexed files"
                }

            file_path = row["file_path"]
            file_type = row["file_type"]
            content = row["content"] or ""
            lines = content.split('\n')

            outline = []

            if file_type == "markdown":
                outline = _extract_markdown_outline(lines)
            elif file_type == "python":
                outline = _extract_python_outline(lines)
            elif file_type in ("javascript", "typescript"):
                outline = _extract_js_ts_outline(lines)
            else:
                outline = _extract_generic_outline(lines)

            return {
                "filepath": file_path,
                "file_type": file_type,
                "line_count": len(lines),
                "outline": outline,
            }

    except sqlite3.Error as e:
        return {"error": f"Database error: {e}"}


def cmd_toc(db_path: Path, file_type: Optional[str] = None) -> dict:
    """
    Generate table of contents for all indexed files.

    For each file, extracts:
    - filepath
    - First header or first non-empty line
    - file_type
    - line_count

    Args:
        db_path: Path to the SQLite database
        file_type: Optional filter by file type

    Returns:
        Dictionary containing:
        - total: Total files
        - filter_type: Applied type filter
        - entries: List of TOC entries sorted alphabetically
    """
    error = _check_db_exists(db_path)
    if error:
        return error

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            if file_type:
                cursor.execute(
                    "SELECT file_path, filename, file_type, content FROM files "
                    "WHERE file_type = ? ORDER BY file_path",
                    (file_type,)
                )
            else:
                cursor.execute(
                    "SELECT file_path, filename, file_type, content FROM files "
                    "ORDER BY file_path"
                )

            entries = []
            for row in cursor.fetchall():
                content = row["content"] or ""
                lines = content.split('\n')
                line_count = len(lines)

                # Extract title/first header
                title = _extract_title(lines, row["file_type"])

                entries.append({
                    "filepath": row["file_path"],
                    "filename": row["filename"],
                    "file_type": row["file_type"],
                    "title": title,
                    "line_count": line_count,
                })

            return {
                "total": len(entries),
                "filter_type": file_type,
                "entries": entries,
            }

    except sqlite3.Error as e:
        return {"error": f"Database error: {e}"}


# ============================================================================
# Helper Functions
# ============================================================================

def _human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _human_readable_duration(delta: timedelta) -> str:
    """Convert timedelta to human-readable string."""
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return f"{seconds} seconds ago"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"


def _extract_title(lines: list[str], file_type: str) -> str:
    """Extract title or first meaningful line from file content."""
    # For markdown, look for first header
    if file_type == "markdown":
        for line in lines[:20]:  # Check first 20 lines
            line = line.strip()
            if line.startswith('#'):
                # Remove leading # and return
                return line.lstrip('#').strip()

    # For Python, look for module docstring or first def/class
    if file_type == "python":
        in_docstring = False
        docstring_content = []
        for i, line in enumerate(lines[:30]):
            stripped = line.strip()

            # Check for docstring
            if i == 0 and stripped.startswith('"""') or stripped.startswith("'''"):
                if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    # Single-line docstring
                    return stripped.strip('"\'').strip()
                in_docstring = True
                docstring_content.append(stripped.strip('"\''))
                continue

            if in_docstring:
                if '"""' in stripped or "'''" in stripped:
                    # End of docstring
                    return ' '.join(docstring_content).strip()
                docstring_content.append(stripped)
                continue

            # Look for first class or function
            if stripped.startswith('class ') or stripped.startswith('def '):
                return stripped

    # For JavaScript/TypeScript, look for JSDoc or first export/function/class
    if file_type in ("javascript", "typescript"):
        in_jsdoc = False
        jsdoc_content = []
        for i, line in enumerate(lines[:30]):
            stripped = line.strip()

            # Check for JSDoc start
            if stripped.startswith('/**'):
                in_jsdoc = True
                # Extract text after /**
                text = stripped[3:].strip()
                if text and not text.startswith('*'):
                    jsdoc_content.append(text)
                continue

            if in_jsdoc:
                if stripped.endswith('*/'):
                    # End of JSDoc
                    if jsdoc_content:
                        return ' '.join(jsdoc_content).strip()[:80]
                    in_jsdoc = False
                    continue
                # Extract text from JSDoc lines (strip leading *)
                if stripped.startswith('*'):
                    text = stripped[1:].strip()
                    # Skip @param, @returns, etc.
                    if text and not text.startswith('@'):
                        jsdoc_content.append(text)
                continue

            # Look for first meaningful declaration
            if any(stripped.startswith(kw) for kw in
                   ('export ', 'function ', 'class ', 'const ', 'interface ', 'type ')):
                return stripped[:80]

    # Generic: return first non-empty, non-comment line
    for line in lines[:10]:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and not stripped.startswith('//'):
            return stripped[:80]

    return "(empty)"


def _extract_markdown_outline(lines: list[str]) -> list[dict]:
    """Extract headers from markdown content."""
    outline = []
    header_pattern = re.compile(r'^(#{1,6})\s+(.+)$')

    for line_num, line in enumerate(lines, start=1):
        match = header_pattern.match(line)
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            outline.append({
                "line": line_num,
                "level": level,
                "type": "header",
                "text": text,
            })

    return outline


def _extract_python_outline(lines: list[str]) -> list[dict]:
    """Extract class and function definitions from Python code."""
    outline = []

    # Patterns for Python constructs
    class_pattern = re.compile(r'^(\s*)class\s+(\w+).*?:')
    func_pattern = re.compile(r'^(\s*)def\s+(\w+)\s*\((.*?)\).*?:')
    async_func_pattern = re.compile(r'^(\s*)async\s+def\s+(\w+)\s*\((.*?)\).*?:')

    for line_num, line in enumerate(lines, start=1):
        # Class definition
        match = class_pattern.match(line)
        if match:
            indent = len(match.group(1))
            level = indent // 4 + 1
            name = match.group(2)
            outline.append({
                "line": line_num,
                "level": level,
                "type": "class",
                "text": f"class {name}",
            })
            continue

        # Async function
        match = async_func_pattern.match(line)
        if match:
            indent = len(match.group(1))
            level = indent // 4 + 1
            name = match.group(2)
            params = match.group(3)
            # Truncate long param lists
            if len(params) > 40:
                params = params[:40] + "..."
            outline.append({
                "line": line_num,
                "level": level,
                "type": "async_function",
                "text": f"async def {name}({params})",
            })
            continue

        # Regular function
        match = func_pattern.match(line)
        if match:
            indent = len(match.group(1))
            level = indent // 4 + 1
            name = match.group(2)
            params = match.group(3)
            # Truncate long param lists
            if len(params) > 40:
                params = params[:40] + "..."
            outline.append({
                "line": line_num,
                "level": level,
                "type": "function",
                "text": f"def {name}({params})",
            })

    return outline


def _extract_js_ts_outline(lines: list[str]) -> list[dict]:
    """Extract class, function, and const declarations from JS/TS code."""
    outline = []

    # Patterns for JS/TS constructs
    class_pattern = re.compile(r'^(\s*)(?:export\s+)?class\s+(\w+)')
    func_pattern = re.compile(r'^(\s*)(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(')
    arrow_const_pattern = re.compile(r'^(\s*)(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(')
    interface_pattern = re.compile(r'^(\s*)(?:export\s+)?interface\s+(\w+)')
    type_pattern = re.compile(r'^(\s*)(?:export\s+)?type\s+(\w+)')

    for line_num, line in enumerate(lines, start=1):
        # Class
        match = class_pattern.match(line)
        if match:
            indent = len(match.group(1))
            level = indent // 2 + 1
            name = match.group(2)
            outline.append({
                "line": line_num,
                "level": level,
                "type": "class",
                "text": f"class {name}",
            })
            continue

        # Function
        match = func_pattern.match(line)
        if match:
            indent = len(match.group(1))
            level = indent // 2 + 1
            name = match.group(2)
            outline.append({
                "line": line_num,
                "level": level,
                "type": "function",
                "text": f"function {name}",
            })
            continue

        # Arrow function const
        match = arrow_const_pattern.match(line)
        if match:
            indent = len(match.group(1))
            level = indent // 2 + 1
            name = match.group(2)
            outline.append({
                "line": line_num,
                "level": level,
                "type": "const_function",
                "text": f"const {name}",
            })
            continue

        # Interface (TypeScript)
        match = interface_pattern.match(line)
        if match:
            indent = len(match.group(1))
            level = indent // 2 + 1
            name = match.group(2)
            outline.append({
                "line": line_num,
                "level": level,
                "type": "interface",
                "text": f"interface {name}",
            })
            continue

        # Type (TypeScript)
        match = type_pattern.match(line)
        if match:
            indent = len(match.group(1))
            level = indent // 2 + 1
            name = match.group(2)
            outline.append({
                "line": line_num,
                "level": level,
                "type": "type",
                "text": f"type {name}",
            })

    return outline


def _extract_generic_outline(lines: list[str]) -> list[dict]:
    """Extract outline from generic file (numbered sections, headers)."""
    outline = []

    # Look for numbered sections (1. 2. etc.) or header-like patterns
    numbered_pattern = re.compile(r'^(\d+\.)+\s+(.+)$')
    bracketed_pattern = re.compile(r'^\[([^\]]+)\]')
    section_pattern = re.compile(r'^#+\s+(.+)$')

    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Numbered sections
        match = numbered_pattern.match(stripped)
        if match:
            # Count dots to determine level
            prefix = match.group(0).split()[0]
            level = prefix.count('.')
            text = match.group(2) if match.lastindex >= 2 else stripped
            outline.append({
                "line": line_num,
                "level": level,
                "type": "numbered_section",
                "text": stripped[:80],
            })
            continue

        # Section headers (# style even in non-markdown)
        match = section_pattern.match(stripped)
        if match:
            level = len(line) - len(line.lstrip('#'))
            outline.append({
                "line": line_num,
                "level": level,
                "type": "section",
                "text": match.group(1).strip(),
            })
            continue

        # Bracketed sections [SECTION]
        match = bracketed_pattern.match(stripped)
        if match:
            outline.append({
                "line": line_num,
                "level": 1,
                "type": "bracketed_section",
                "text": match.group(1),
            })

    return outline
