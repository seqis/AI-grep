#!/usr/bin/env python3
"""
Multi-Directory Source Management - Support for indexing multiple directories.

This module provides commands to mount, list, and unmount external directories
for indexing. Each mounted directory (source) is tracked separately, allowing
the search tool to span multiple locations transparently.

Schema additions:
    CREATE TABLE sources (
        source_id INTEGER PRIMARY KEY AUTOINCREMENT,
        alias TEXT UNIQUE NOT NULL,
        absolute_path TEXT UNIQUE NOT NULL,
        added_at TEXT NOT NULL,
        last_indexed_at TEXT,
        file_count INTEGER DEFAULT 0
    );

    -- files table gets a nullable source_id column for backward compatibility
    ALTER TABLE files ADD COLUMN source_id INTEGER REFERENCES sources(source_id);

Usage:
    from vault_lib.sources import cmd_mount, cmd_sources, cmd_unmount

    # Mount a directory
    result = cmd_mount(db_path, "/path/to/docs", "docs")

    # List sources
    sources = cmd_sources(db_path)

    # Unmount a source
    result = cmd_unmount(db_path, "docs")
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional


@contextmanager
def _get_connection(db_path: Path):
    """Context manager for database connections with Row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    """Check if a table exists in the database."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def _column_exists(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row["name"] for row in cursor.fetchall()]
    return column_name in columns


def ensure_sources_schema(db_path: Path) -> dict:
    """
    Ensure the sources table exists and files table has source_id column.

    This function handles schema migration gracefully:
    - Creates sources table if it doesn't exist
    - Adds source_id column to files table if missing (nullable for backward compat)

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Dictionary with migration status:
        - success: bool
        - sources_table_created: bool
        - source_id_column_added: bool
        - error: str or None
    """
    result = {
        "success": False,
        "sources_table_created": False,
        "source_id_column_added": False,
        "error": None,
    }

    if not db_path.exists():
        result["error"] = f"Database not found: {db_path}"
        return result

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Create sources table if it doesn't exist
            if not _table_exists(cursor, "sources"):
                cursor.execute("""
                    CREATE TABLE sources (
                        source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alias TEXT UNIQUE NOT NULL,
                        absolute_path TEXT UNIQUE NOT NULL,
                        added_at TEXT NOT NULL,
                        last_indexed_at TEXT,
                        file_count INTEGER DEFAULT 0
                    )
                """)
                result["sources_table_created"] = True

            # Add source_id column to files table if missing
            if _table_exists(cursor, "files"):
                if not _column_exists(cursor, "files", "source_id"):
                    cursor.execute("""
                        ALTER TABLE files ADD COLUMN source_id INTEGER
                        REFERENCES sources(source_id)
                    """)
                    result["source_id_column_added"] = True

            conn.commit()
            result["success"] = True

    except sqlite3.Error as e:
        result["error"] = f"Database error: {e}"

    return result


def cmd_mount(db_path: Path, path: str, alias: str) -> dict:
    """
    Mount an external directory for indexing.

    Validates that the path exists and is a directory, then stores it in the
    sources table. Does not index on mount - user runs index separately.

    Args:
        db_path: Path to the SQLite database file
        path: Filesystem path to the directory to mount
        alias: Unique alias for this source (e.g., "docs", "projects")

    Returns:
        Dictionary with:
        - success: bool
        - message: str - Human-readable status message
        - source_id: int - ID of the created source (if successful)
        - error: str or None
    """
    result = {
        "success": False,
        "message": "",
        "source_id": None,
        "error": None,
    }

    # Validate path
    dir_path = Path(path).resolve()

    if not dir_path.exists():
        result["error"] = f"Path does not exist: {path}"
        result["message"] = f"Error: Path does not exist: {path}"
        return result

    if not dir_path.is_dir():
        result["error"] = f"Path is not a directory: {path}"
        result["message"] = f"Error: Path is not a directory: {path}"
        return result

    # Validate alias
    if not alias or not alias.strip():
        result["error"] = "Alias cannot be empty"
        result["message"] = "Error: Alias cannot be empty"
        return result

    alias = alias.strip()

    # Ensure schema is ready
    schema_result = ensure_sources_schema(db_path)
    if not schema_result["success"]:
        result["error"] = schema_result["error"]
        result["message"] = f"Error: {schema_result['error']}"
        return result

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Check if alias already exists
            cursor.execute("SELECT alias FROM sources WHERE alias = ?", (alias,))
            if cursor.fetchone():
                result["error"] = f"Alias '{alias}' already exists"
                result["message"] = f"Error: Alias '{alias}' is already in use"
                return result

            # Check if path already mounted
            cursor.execute(
                "SELECT alias FROM sources WHERE absolute_path = ?",
                (str(dir_path),)
            )
            existing = cursor.fetchone()
            if existing:
                result["error"] = f"Path already mounted as '{existing['alias']}'"
                result["message"] = (
                    f"Error: Path is already mounted with alias '{existing['alias']}'"
                )
                return result

            # Insert new source
            now = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT INTO sources (alias, absolute_path, added_at, file_count)
                VALUES (?, ?, ?, 0)
                """,
                (alias, str(dir_path), now)
            )

            source_id = cursor.lastrowid
            conn.commit()

            result["success"] = True
            result["source_id"] = source_id
            result["message"] = (
                f"Mounted '{alias}' -> {dir_path}\n"
                f"Run 'index' to index files from this source."
            )

    except sqlite3.Error as e:
        result["error"] = f"Database error: {e}"
        result["message"] = f"Error: Database error: {e}"

    return result


def cmd_sources(db_path: Path) -> dict:
    """
    List all mounted directories with their status.

    For each source, returns:
    - alias: The user-defined alias
    - absolute_path: The filesystem path
    - file_count: Number of indexed files from this source
    - last_indexed_at: When the source was last indexed (or null)
    - exists: Whether the path still exists on the filesystem

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Dictionary with:
        - success: bool
        - sources: List of source dictionaries
        - error: str or None
    """
    result = {
        "success": False,
        "sources": [],
        "error": None,
    }

    if not db_path.exists():
        result["error"] = f"Database not found: {db_path}"
        return result

    # Ensure schema exists
    schema_result = ensure_sources_schema(db_path)
    if not schema_result["success"]:
        result["error"] = schema_result["error"]
        return result

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Get all sources
            cursor.execute("""
                SELECT
                    source_id,
                    alias,
                    absolute_path,
                    added_at,
                    last_indexed_at,
                    file_count
                FROM sources
                ORDER BY alias
            """)

            sources = []
            for row in cursor.fetchall():
                path = Path(row["absolute_path"])
                source = {
                    "source_id": row["source_id"],
                    "alias": row["alias"],
                    "absolute_path": row["absolute_path"],
                    "added_at": row["added_at"],
                    "last_indexed_at": row["last_indexed_at"],
                    "file_count": row["file_count"],
                    "exists": path.exists() and path.is_dir(),
                }
                sources.append(source)

            # Also get actual file counts from files table (more accurate)
            for source in sources:
                cursor.execute(
                    "SELECT COUNT(*) as count FROM files WHERE source_id = ?",
                    (source["source_id"],)
                )
                count_row = cursor.fetchone()
                if count_row:
                    source["file_count"] = count_row["count"]

            result["success"] = True
            result["sources"] = sources

    except sqlite3.Error as e:
        result["error"] = f"Database error: {e}"

    return result


def cmd_unmount(db_path: Path, alias: str) -> dict:
    """
    Unmount a directory and remove all its indexed files.

    Removes the source from the sources table and deletes all files
    associated with that source from the files table (and FTS index).

    Args:
        db_path: Path to the SQLite database file
        alias: Alias of the source to unmount

    Returns:
        Dictionary with:
        - success: bool
        - message: str - Human-readable status message
        - files_removed: int - Number of files removed from index
        - error: str or None
    """
    result = {
        "success": False,
        "message": "",
        "files_removed": 0,
        "error": None,
    }

    if not alias or not alias.strip():
        result["error"] = "Alias cannot be empty"
        result["message"] = "Error: Alias cannot be empty"
        return result

    alias = alias.strip()

    if not db_path.exists():
        result["error"] = f"Database not found: {db_path}"
        result["message"] = f"Error: Database not found: {db_path}"
        return result

    # Ensure schema exists
    schema_result = ensure_sources_schema(db_path)
    if not schema_result["success"]:
        result["error"] = schema_result["error"]
        result["message"] = f"Error: {schema_result['error']}"
        return result

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Find the source
            cursor.execute(
                "SELECT source_id, absolute_path FROM sources WHERE alias = ?",
                (alias,)
            )
            source = cursor.fetchone()

            if not source:
                result["error"] = f"Source '{alias}' not found"
                result["message"] = f"Error: No source found with alias '{alias}'"
                return result

            source_id = source["source_id"]
            source_path = source["absolute_path"]

            # Count files to be removed
            cursor.execute(
                "SELECT COUNT(*) as count FROM files WHERE source_id = ?",
                (source_id,)
            )
            files_count = cursor.fetchone()["count"]

            # Begin transaction for atomic removal
            cursor.execute("BEGIN IMMEDIATE")

            try:
                # Get file IDs for FTS cleanup
                cursor.execute(
                    "SELECT file_id, file_path, filename, content FROM files WHERE source_id = ?",
                    (source_id,)
                )
                files_to_delete = cursor.fetchall()

                # Remove from FTS index first (using delete trigger workaround)
                for file_row in files_to_delete:
                    cursor.execute(
                        """
                        INSERT INTO files_fts(files_fts, rowid, file_path, filename, content)
                        VALUES('delete', ?, ?, ?, ?)
                        """,
                        (file_row["file_id"], file_row["file_path"],
                         file_row["filename"], file_row["content"])
                    )

                # Delete files from files table
                cursor.execute(
                    "DELETE FROM files WHERE source_id = ?",
                    (source_id,)
                )

                # Delete the source
                cursor.execute(
                    "DELETE FROM sources WHERE source_id = ?",
                    (source_id,)
                )

                cursor.execute("COMMIT")

                result["success"] = True
                result["files_removed"] = files_count
                result["message"] = (
                    f"Unmounted '{alias}' ({source_path})\n"
                    f"Removed {files_count} indexed files."
                )

            except Exception as e:
                cursor.execute("ROLLBACK")
                raise e

    except sqlite3.Error as e:
        result["error"] = f"Database error: {e}"
        result["message"] = f"Error: Database error: {e}"

    return result


def get_all_source_paths(db_path: Path) -> list[tuple[Optional[int], str]]:
    """
    Get all source paths for indexing.

    Returns a list of (source_id, path) tuples. For backward compatibility,
    includes (None, ".") for the local directory if no sources are mounted.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        List of tuples: (source_id, absolute_path)
        - source_id is None for local (implicit) source
        - source_id is int for mounted sources
    """
    result = []

    if not db_path.exists():
        # No database yet, return local only
        return [(None, ".")]

    # Ensure schema exists
    schema_result = ensure_sources_schema(db_path)
    if not schema_result["success"]:
        return [(None, ".")]

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            # Check if sources table has any entries
            cursor.execute("SELECT source_id, absolute_path FROM sources ORDER BY alias")
            sources = cursor.fetchall()

            if sources:
                for row in sources:
                    result.append((row["source_id"], row["absolute_path"]))
            else:
                # No mounted sources, use local directory
                result.append((None, "."))

    except sqlite3.Error:
        result = [(None, ".")]

    return result


def update_source_stats(db_path: Path, source_id: int, file_count: int) -> bool:
    """
    Update the file count and last_indexed_at for a source.

    Called by the indexer after indexing files from a source.

    Args:
        db_path: Path to the SQLite database file
        source_id: ID of the source to update
        file_count: Number of files indexed from this source

    Returns:
        True if update succeeded, False otherwise
    """
    if not db_path.exists():
        return False

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            now = datetime.now().isoformat()
            cursor.execute(
                """
                UPDATE sources
                SET file_count = ?, last_indexed_at = ?
                WHERE source_id = ?
                """,
                (file_count, now, source_id)
            )

            conn.commit()
            return cursor.rowcount > 0

    except sqlite3.Error:
        return False


def get_source_by_alias(db_path: Path, alias: str) -> Optional[dict]:
    """
    Get a source by its alias.

    Args:
        db_path: Path to the SQLite database file
        alias: Alias of the source to find

    Returns:
        Source dictionary or None if not found
    """
    if not db_path.exists():
        return None

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT source_id, alias, absolute_path, added_at,
                       last_indexed_at, file_count
                FROM sources
                WHERE alias = ?
                """,
                (alias,)
            )

            row = cursor.fetchone()
            if row:
                path = Path(row["absolute_path"])
                return {
                    "source_id": row["source_id"],
                    "alias": row["alias"],
                    "absolute_path": row["absolute_path"],
                    "added_at": row["added_at"],
                    "last_indexed_at": row["last_indexed_at"],
                    "file_count": row["file_count"],
                    "exists": path.exists() and path.is_dir(),
                }

    except sqlite3.Error:
        pass

    return None


def get_source_by_id(db_path: Path, source_id: int) -> Optional[dict]:
    """
    Get a source by its ID.

    Args:
        db_path: Path to the SQLite database file
        source_id: ID of the source to find

    Returns:
        Source dictionary or None if not found
    """
    if not db_path.exists():
        return None

    try:
        with _get_connection(db_path) as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT source_id, alias, absolute_path, added_at,
                       last_indexed_at, file_count
                FROM sources
                WHERE source_id = ?
                """,
                (source_id,)
            )

            row = cursor.fetchone()
            if row:
                path = Path(row["absolute_path"])
                return {
                    "source_id": row["source_id"],
                    "alias": row["alias"],
                    "absolute_path": row["absolute_path"],
                    "added_at": row["added_at"],
                    "last_indexed_at": row["last_indexed_at"],
                    "file_count": row["file_count"],
                    "exists": path.exists() and path.is_dir(),
                }

    except sqlite3.Error:
        pass

    return None


# CLI entry point for testing
if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Multi-directory source management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Mount a directory
    python sources.py mount /path/to/docs docs

    # List sources
    python sources.py sources

    # Unmount a source
    python sources.py unmount docs

    # Show schema migration status
    python sources.py migrate
        """,
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=Path(".") / "SEARCH" / ".vault.db",
        help="Path to database (default: ./SEARCH/.vault.db)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # mount command
    mount_parser = subparsers.add_parser("mount", help="Mount a directory")
    mount_parser.add_argument("path", help="Directory path to mount")
    mount_parser.add_argument("alias", help="Alias for this source")

    # sources command
    subparsers.add_parser("sources", help="List mounted sources")

    # unmount command
    unmount_parser = subparsers.add_parser("unmount", help="Unmount a source")
    unmount_parser.add_argument("alias", help="Alias of source to unmount")

    # migrate command
    subparsers.add_parser("migrate", help="Run schema migration")

    args = parser.parse_args()

    if args.command == "mount":
        result = cmd_mount(args.db, args.path, args.alias)
        print(result["message"])
        sys.exit(0 if result["success"] else 1)

    elif args.command == "sources":
        result = cmd_sources(args.db)
        if result["success"]:
            if not result["sources"]:
                print("No sources mounted.")
            else:
                print(f"{'Alias':<15} {'Path':<50} {'Files':>8} {'Exists':>8}")
                print("-" * 85)
                for src in result["sources"]:
                    exists_str = "Yes" if src["exists"] else "NO"
                    print(
                        f"{src['alias']:<15} "
                        f"{src['absolute_path']:<50} "
                        f"{src['file_count']:>8} "
                        f"{exists_str:>8}"
                    )
        else:
            print(f"Error: {result['error']}")
            sys.exit(1)

    elif args.command == "unmount":
        result = cmd_unmount(args.db, args.alias)
        print(result["message"])
        sys.exit(0 if result["success"] else 1)

    elif args.command == "migrate":
        result = ensure_sources_schema(args.db)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["success"] else 1)

    else:
        parser.print_help()
        sys.exit(1)
