"""
Vault File Index - Incremental indexing of files into SQLite FTS5.

This module provides incremental indexing of individual files in a directory
into SQLite with FTS5 full-text search. Unlike db.py which indexes the
concatenated vault format, this module indexes arbitrary files directly.

Key features:
- Incremental updates (only index changed files)
- FTS5 full-text search on file content
- Content hashing for change detection
- Auto-exclude patterns (.searchignore support)
- Manifest tracking for staleness detection
"""

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from vault_lib.sections import extract_sections


# Default exclusion patterns
DEFAULT_EXCLUDES = [
    "./SEARCH",
    "./SEARCH/*",
    "./.vault_state",
    "./.vault_state/*",
    "./.git",
    "./.git/*",
    "__pycache__",
    "__pycache__/*",
    "*.pyc",
    ".DS_Store",
    "*.db",
    "*.db-journal",
]


def init_db(db_path: Path) -> None:
    """
    Initialize the SQLite database schema.

    Note: The main files/files_fts tables are created by setup.py.
    This ensures the manifest table exists for tracking index state.

    Args:
        db_path: Path to the SQLite database file
    """
    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Manifest table for tracking index state (created by index.py, not setup.py)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS manifest (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_indexed_at TEXT NOT NULL,
                total_files INTEGER NOT NULL,
                content_hash TEXT NOT NULL
            )
        """)

        conn.commit()


@contextmanager
def _get_connection(db_path: Path):
    """Context manager for database connections."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_indexed_files(db_path: Path) -> dict[str, str]:
    """
    Get all indexed files with their content hashes.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Dictionary mapping file_path (relative) to content_hash
    """
    if not db_path.exists():
        return {}

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Check if files table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='files'
        """)
        if not cursor.fetchone():
            return {}

        cursor.execute("SELECT file_path, content_hash FROM files")
        rows = cursor.fetchall()
        return {row["file_path"]: row["content_hash"] for row in rows}


def calculate_file_hash(file_path: Path) -> str:
    """
    Calculate SHA-256 hash of file content.

    Args:
        file_path: Path to the file

    Returns:
        16-character hex string of the content hash
    """
    content = file_path.read_bytes()
    return hashlib.sha256(content).hexdigest()[:16]


def _read_searchignore(root_path: Path) -> list[str]:
    """
    Read .searchignore file if it exists.

    Args:
        root_path: Root directory to search for .searchignore

    Returns:
        List of patterns from .searchignore, or empty list if not found
    """
    ignore_file = root_path / ".searchignore"
    if not ignore_file.exists():
        return []

    patterns = []
    for line in ignore_file.read_text().splitlines():
        line = line.strip()
        # Skip comments and empty lines
        if line and not line.startswith("#"):
            patterns.append(line)

    return patterns


def _should_exclude(file_path: Path, root_path: Path, exclude_patterns: list[str]) -> bool:
    """
    Check if a file should be excluded from indexing.

    Supports:
    - Glob patterns (*.log, *.pyc)
    - Directory patterns (secrets/, __pycache__/)
    - Path patterns (./SEARCH/*, subdir/*)

    Args:
        file_path: Absolute path to the file
        root_path: Root directory for relative path calculation
        exclude_patterns: List of glob patterns to exclude

    Returns:
        True if file should be excluded
    """
    try:
        rel_path = file_path.relative_to(root_path)
    except ValueError:
        return True  # File is outside root, exclude

    rel_str = str(rel_path)
    rel_parts = rel_path.parts

    for pattern in exclude_patterns:
        # Handle patterns with and without leading ./
        pattern_clean = pattern.lstrip("./").rstrip("/")

        # Check if this is a directory pattern (ends with /)
        is_dir_pattern = pattern.endswith("/")

        if is_dir_pattern:
            # For directory patterns, check if any part of the path matches
            for part in rel_parts[:-1]:  # Check all directories (not the filename)
                if fnmatch(part, pattern_clean):
                    return True
            # Also check the full path prefix
            for i in range(len(rel_parts) - 1):
                prefix = "/".join(rel_parts[:i+1])
                if fnmatch(prefix, pattern_clean):
                    return True
                if fnmatch(prefix, pattern_clean + "/*"):
                    return True
        else:
            # Match against relative path
            if fnmatch(rel_str, pattern):
                return True
            if fnmatch(rel_str, pattern_clean):
                return True

            # Also match against just the filename
            if fnmatch(file_path.name, pattern):
                return True
            if fnmatch(file_path.name, pattern_clean):
                return True

            # Match parent directories for path patterns like "subdir/*"
            for parent in rel_path.parents:
                parent_str = str(parent)
                if parent_str == ".":
                    continue
                if fnmatch(parent_str, pattern):
                    return True
                if fnmatch(parent_str, pattern_clean):
                    return True

    return False


def _get_file_type(file_path: Path) -> str:
    """
    Determine file type from extension.

    Args:
        file_path: Path to the file

    Returns:
        File type string (e.g., 'markdown', 'python', 'text')
    """
    ext = file_path.suffix.lower()
    type_map = {
        ".md": "markdown",
        ".txt": "text",
        ".py": "python",
        ".sh": "shell",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".html": "html",
        ".css": "css",
        ".js": "javascript",
        ".ts": "typescript",
    }
    return type_map.get(ext, "unknown")


def _read_file_content(file_path: Path) -> Optional[str]:
    """
    Read file content as text, handling encoding errors.

    Args:
        file_path: Path to the file

    Returns:
        File content as string, or None if unreadable
    """
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return file_path.read_text(encoding="latin-1")
        except Exception:
            return None
    except Exception:
        return None


def index_files(
    root_path: Path,
    db_path: Path,
    exclude_patterns: Optional[list[str]] = None,
    verbose: bool = False,
) -> dict:
    """
    Incrementally index files from a directory into SQLite FTS5.

    This function:
    1. Scans the directory for files
    2. Calculates content hashes
    3. Compares with existing index
    4. Updates only changed files

    Args:
        root_path: Root directory to index
        db_path: Path to the SQLite database
        exclude_patterns: Additional patterns to exclude (merged with defaults)
        verbose: Print progress information

    Returns:
        Dictionary with indexing statistics:
        - added: Number of new files indexed
        - updated: Number of changed files re-indexed
        - deleted: Number of removed files cleaned up
        - unchanged: Number of files unchanged
        - errors: List of files that failed to index
    """
    # Initialize database
    init_db(db_path)

    # Build exclusion patterns
    patterns = list(DEFAULT_EXCLUDES)
    patterns.extend(_read_searchignore(root_path))
    if exclude_patterns:
        patterns.extend(exclude_patterns)

    if verbose:
        print(f"Indexing files in: {root_path}")
        print(f"Exclusion patterns: {len(patterns)}")

    # Get current index state
    indexed_files = get_indexed_files(db_path)
    if verbose:
        print(f"Currently indexed: {len(indexed_files)} files")

    # Scan directory for files
    current_files = {}  # file_path (relative) -> content_hash
    errors = []

    for file_path in root_path.rglob("*"):
        if not file_path.is_file():
            continue

        if _should_exclude(file_path, root_path, patterns):
            continue

        try:
            rel_path = str(file_path.relative_to(root_path))
            content_hash = calculate_file_hash(file_path)
            current_files[rel_path] = content_hash
        except Exception as e:
            errors.append({"file": str(file_path), "error": str(e)})

    if verbose:
        print(f"Found {len(current_files)} files to index")

    # Determine changes
    indexed_set = set(indexed_files.keys())
    current_set = set(current_files.keys())

    to_add = current_set - indexed_set
    to_delete = indexed_set - current_set
    to_check = indexed_set & current_set

    # Check for content changes in existing files
    to_update = set()
    for file_path in to_check:
        if current_files[file_path] != indexed_files[file_path]:
            to_update.add(file_path)

    unchanged = len(to_check) - len(to_update)

    if verbose:
        print(f"Changes: +{len(to_add)} added, ~{len(to_update)} updated, -{len(to_delete)} deleted, ={unchanged} unchanged")

    # Apply changes to database
    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        try:
            cursor.execute("BEGIN IMMEDIATE")

            # Delete removed files from FTS first, then from files table
            for file_path in to_delete:
                cursor.execute(
                    "SELECT file_id FROM files WHERE file_path = ?",
                    (file_path,)
                )
                row = cursor.fetchone()
                if row:
                    cursor.execute(
                        "INSERT INTO files_fts(files_fts, rowid, file_path, content) VALUES('delete', ?, ?, ?)",
                        (row["file_id"], file_path, "")
                    )
                cursor.execute("DELETE FROM files WHERE file_path = ?", (file_path,))

            # Delete changed files from FTS before re-inserting
            for file_path in to_update:
                cursor.execute(
                    "SELECT file_id FROM files WHERE file_path = ?",
                    (file_path,)
                )
                row = cursor.fetchone()
                if row:
                    file_id = row["file_id"]
                    cursor.execute(
                        "INSERT INTO files_fts(files_fts, rowid, file_path, content) VALUES('delete', ?, ?, ?)",
                        (file_id, file_path, "")
                    )
                    # Delete old sections (CASCADE should handle this, but explicit for safety)
                    cursor.execute("DELETE FROM file_sections WHERE file_id = ?", (file_id,))
                cursor.execute("DELETE FROM files WHERE file_path = ?", (file_path,))

            # Insert new and updated files
            now = datetime.now().isoformat()
            files_to_insert = to_add | to_update

            for file_path in files_to_insert:
                abs_path = root_path / file_path
                content = _read_file_content(abs_path)

                if content is None:
                    errors.append({"file": file_path, "error": "Could not read file content"})
                    continue

                file_type = _get_file_type(abs_path)
                file_size = abs_path.stat().st_size
                content_hash = current_files[file_path]

                # Insert into files table
                filename = abs_path.name
                cursor.execute("""
                    INSERT INTO files (file_path, filename, file_type, content, content_hash, file_size, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (file_path, filename, file_type, content, content_hash, file_size, now))

                # Get the rowid and insert into FTS
                rowid = cursor.lastrowid
                cursor.execute("""
                    INSERT INTO files_fts(rowid, file_path, filename, content)
                    VALUES (?, ?, ?, ?)
                """, (rowid, file_path, filename, content))

                # Extract and insert sections for all text-based files
                sections = extract_sections(content, file_type)
                for section in sections:
                    cursor.execute("""
                        INSERT INTO file_sections (file_id, line_start, line_end, section_date, section_header, section_type)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        rowid,
                        section["line_start"],
                        section.get("line_end"),
                        section.get("section_date"),
                        section.get("section_header"),
                        section.get("section_type"),
                    ))

            # Update manifest
            all_hashes = sorted(current_files.values())
            combined_hash = hashlib.sha256("".join(all_hashes).encode()).hexdigest()[:16]

            cursor.execute("""
                INSERT OR REPLACE INTO manifest (id, last_indexed_at, total_files, content_hash)
                VALUES (1, ?, ?, ?)
            """, (now, len(current_files), combined_hash))

            cursor.execute("COMMIT")

        except Exception as e:
            cursor.execute("ROLLBACK")
            raise RuntimeError(f"Indexing failed, rolled back: {e}") from e

    if verbose:
        print(f"Indexing complete: {len(to_add)} added, {len(to_update)} updated, {len(to_delete)} deleted")

    return {
        "added": len(to_add),
        "updated": len(to_update),
        "deleted": len(to_delete),
        "unchanged": unchanged,
        "total": len(current_files),
        "errors": errors,
    }


def update_manifest(db_path: Path, file_count: int) -> None:
    """
    Update the manifest with current indexing state.

    Args:
        db_path: Path to the SQLite database
        file_count: Total number of indexed files
    """
    if not db_path.exists():
        return

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Calculate content hash from all file hashes
        cursor.execute("SELECT content_hash FROM files ORDER BY file_path")
        all_hashes = [row["content_hash"] for row in cursor.fetchall()]
        combined_hash = hashlib.sha256("".join(all_hashes).encode()).hexdigest()[:16]

        now = datetime.now().isoformat()

        cursor.execute("""
            INSERT OR REPLACE INTO manifest (id, last_indexed_at, total_files, content_hash)
            VALUES (1, ?, ?, ?)
        """, (now, file_count, combined_hash))

        conn.commit()


def is_stale(db_path: Path, threshold_minutes: int = 60) -> bool:
    """
    Check if the index is stale (older than threshold).

    Args:
        db_path: Path to the SQLite database
        threshold_minutes: Age threshold in minutes (default 60)

    Returns:
        True if index is stale or doesn't exist
    """
    if not db_path.exists():
        return True

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Check if manifest table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='manifest'
        """)
        if not cursor.fetchone():
            return True

        cursor.execute("SELECT last_indexed_at FROM manifest WHERE id = 1")
        row = cursor.fetchone()

        if not row:
            return True

        try:
            last_indexed = datetime.fromisoformat(row["last_indexed_at"])
        except (ValueError, TypeError):
            return True

        age = datetime.now() - last_indexed
        return age > timedelta(minutes=threshold_minutes)


def get_index_stats(db_path: Path) -> Optional[dict]:
    """
    Get statistics about the current index.

    Args:
        db_path: Path to the SQLite database

    Returns:
        Dictionary with index statistics, or None if index doesn't exist
    """
    if not db_path.exists():
        return None

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Get file count
        cursor.execute("SELECT COUNT(*) as count FROM files")
        file_count = cursor.fetchone()["count"]

        # Get manifest info
        cursor.execute("SELECT * FROM manifest WHERE id = 1")
        manifest = cursor.fetchone()

        # Get file type breakdown
        cursor.execute("""
            SELECT file_type, COUNT(*) as count
            FROM files
            GROUP BY file_type
            ORDER BY count DESC
        """)
        type_breakdown = {row["file_type"]: row["count"] for row in cursor.fetchall()}

        # Calculate index age
        age_hours = None
        if manifest:
            try:
                last_indexed = datetime.fromisoformat(manifest["last_indexed_at"])
                age_hours = round((datetime.now() - last_indexed).total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                pass

        return {
            "file_count": file_count,
            "manifest": dict(manifest) if manifest else None,
            "type_breakdown": type_breakdown,
            "age_hours": age_hours,
            "db_path": str(db_path),
            "db_size_kb": round(db_path.stat().st_size / 1024, 1) if db_path.exists() else 0,
        }


def search_index(
    db_path: Path,
    query: str,
    limit: int = 50,
    file_type: Optional[str] = None,
) -> list[dict]:
    """
    Search the FTS5 index.

    Args:
        db_path: Path to the SQLite database
        query: FTS5 search query
        limit: Maximum results to return
        file_type: Optional filter by file type

    Returns:
        List of matching files with snippets
    """
    if not db_path.exists():
        return []

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        if file_type:
            cursor.execute("""
                SELECT
                    f.file_path,
                    f.file_type,
                    f.file_size,
                    f.indexed_at,
                    snippet(files_fts, 1, '<mark>', '</mark>', '...', 32) as snippet,
                    bm25(files_fts) as score
                FROM files_fts
                JOIN files f ON files_fts.rowid = f.file_id
                WHERE files_fts MATCH ? AND f.file_type = ?
                ORDER BY score
                LIMIT ?
            """, (query, file_type, limit))
        else:
            cursor.execute("""
                SELECT
                    f.file_path,
                    f.file_type,
                    f.file_size,
                    f.indexed_at,
                    snippet(files_fts, 1, '<mark>', '</mark>', '...', 32) as snippet,
                    bm25(files_fts) as score
                FROM files_fts
                JOIN files f ON files_fts.rowid = f.file_id
                WHERE files_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """, (query, limit))

        return [dict(row) for row in cursor.fetchall()]
