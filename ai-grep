#!/usr/bin/env python3
"""
AI-grep - Token-Efficient Directory Search for AI Assistants (v1.0.0)

Drop this tool into any directory to enable powerful full-text search.
Designed for AI/LLM token efficiency - find what you need without reading entire files.

QUICK START
-----------
    ./ai-grep setup              # Initialize and index (one-time)
    ./ai-grep search "keyword"   # Find files containing keyword
    ./ai-grep stats              # See what's in the codebase

COMMANDS
--------
Setup & Maintenance:
    setup [--no-index]         Initialize database and index all files
    status                     Show index statistics
    index [--force]            Re-index files (auto-runs on stale DB)
    validate                   Check database integrity
    config                     Show current configuration

Search & Retrieval:
    search <query>             Find files matching query (--limit N)
    get <filepath>             Get full content (--raw, --lines N-M)
    bundle <paths>             Get multiple files (comma-separated)
    context <filepath>         Get lines around a line (--line N --around M)
    list                       List indexed files (--type, --pattern, --recent)

Analysis & Discovery:
    stats                      Codebase statistics (files, sizes, types)
    timeline                   Files by modification date (--days, --limit)
    tags                       Extract frontmatter, hashtags, TODO markers
    outline <file>             Show file structure (headers, functions)
    toc                        Table of contents for all files (--type)

Content Analysis:
    related <file>             Find similar files using TF-IDF (--top N)
    duplicates                 Find duplicate/near-duplicate files
    links                      Extract and validate internal links
    refs <symbol>              Find all references to a symbol (--context N)

Multi-Source (index multiple directories):
    mount <path> <alias>       Add external directory to index
    sources                    List all mounted directories
    unmount <alias>            Remove directory from index

Export & Integration:
    export <query>             Export search results (--format json|csv|md)
    clip <file>                Copy file content to clipboard
    open <file>                Open in editor (--line N)
    history                    Show past queries (--limit, --clear)

Search Enhancements:
    diff                       Show changes since last index (--json)
    grep-context <pattern>     Pattern search with context (--context N)
    relevant <query>           Top files for a query (--top N, token-efficient)

EXAMPLES
--------
    # Understand the codebase
    ./ai-grep stats
    ./ai-grep toc --type python
    ./ai-grep outline src/main.py

    # Find what you need
    ./ai-grep search "database connection"
    ./ai-grep relevant "authentication" --top 5
    ./ai-grep refs "UserModel"

    # Get content efficiently
    ./ai-grep get "config.py" --lines 50-100
    ./ai-grep context "main.py" --line 42 --around 10
    ./ai-grep clip "utils.py"

    # Content analysis
    ./ai-grep related "src/auth.py" --top 3
    ./ai-grep duplicates
    ./ai-grep links

    # Multi-directory search
    ./ai-grep mount ~/notes notes
    ./ai-grep mount ~/code code
    ./ai-grep sources
    ./ai-grep search "project ideas"  # Searches all mounted dirs

    # Track changes
    ./ai-grep diff
    ./ai-grep timeline --days 7

AI/LLM WORKFLOW
---------------
    1. Orient:    ./ai-grep stats && ./ai-grep toc
    2. Locate:    ./ai-grep relevant "your task" --top 5
    3. Retrieve:  ./ai-grep get "path.py" --lines 10-50
    4. Analyze:   ./ai-grep related "path.py" --top 3

TOKEN SAVINGS
-------------
    - search returns snippets (~300 chars) not full files
    - relevant returns just paths + scores (minimal tokens)
    - get --lines N-M fetches specific ranges
    - outline shows structure without full content
    - toc gives overview without reading files
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from vault_lib.setup import run_setup, validate_setup, check_dependencies
from vault_lib.index import init_db, index_files, is_stale, get_index_stats

# New module imports for enhanced features
from vault_lib.analysis import cmd_stats, cmd_timeline, cmd_tags, cmd_outline, cmd_toc
from vault_lib.similarity import cmd_related, cmd_duplicates, cmd_links, cmd_refs
from vault_lib.sources import cmd_mount, cmd_sources, cmd_unmount, ensure_sources_schema
from vault_lib.export import cmd_export, cmd_clip, cmd_open, cmd_history, log_query
from vault_lib.search import cmd_diff, cmd_grep_context, cmd_relevant
from vault_lib.search import search_combined, search_fts, search_ripgrep
from vault_lib.file_extract import walk_directory


# Configuration
SEARCH_DIR = Path.cwd() / "SEARCH"
DB_PATH = SEARCH_DIR / ".vault.db"
CONFIG_PATH = SEARCH_DIR / "config.json"
MANIFEST_PATH = SEARCH_DIR / ".vault-manifest.json"


def cmd_setup(args):
    """Initialize database, dependencies, and perform initial index."""
    print("Setting up search in:", Path.cwd())
    print()

    result = run_setup(Path.cwd())

    if result.get("success"):
        print("\n✓ Setup complete!")
        print(f"  Database: {result.get('db_path')}")
        print(f"  Config: {result.get('config_path')}")

        # Auto-index after setup (unless --no-index flag)
        if not getattr(args, 'no_index', False):
            print("\nIndexing files...")
            try:
                exclude_patterns = []
                if CONFIG_PATH.exists():
                    config = json.loads(CONFIG_PATH.read_text())
                    exclude_patterns = config.get("exclude_rules", [])

                index_result = index_files(
                    Path.cwd(),
                    DB_PATH,
                    exclude_patterns=exclude_patterns,
                    verbose=False
                )
                print(f"✓ Indexed {index_result['total']} files")
                if index_result.get("errors"):
                    print(f"  Warnings: {len(index_result['errors'])} files had issues")
            except Exception as e:
                print(f"⚠ Indexing failed: {e}")
                print("  You can manually run './ai-grep index' later")

        print("\nReady! Try:")
        print("  ./ai-grep search <query>    # Search files")
        print("  ./ai-grep list              # See indexed files")
        print("  ./ai-grep get <file>        # View file content")
        return 0
    else:
        print("\n✗ Setup failed:")
        for error in result.get("errors", []):
            print(f"  - {error}")
        if result.get("warnings"):
            print("\nWarnings:")
            for warning in result["warnings"]:
                print(f"  - {warning}")
        return 1


def cmd_status(args):
    """Show index info and statistics."""
    if not DB_PATH.exists():
        print(json.dumps({
            "error": "Database not initialized",
            "hint": "Run './ai-grep setup' first"
        }))
        return 1

    try:
        stats = get_index_stats(DB_PATH)

        result = {
            "search_dir": str(SEARCH_DIR),
            "cwd": str(Path.cwd()),
            "database": {
                "path": str(DB_PATH),
                "exists": True,
                "size_mb": round(DB_PATH.stat().st_size / (1024 * 1024), 2),
            },
            "index": stats,
        }

        # Add staleness info
        if is_stale(DB_PATH, threshold_minutes=30):
            result["status"] = "stale (>30 min old)"
        else:
            result["status"] = "current"

        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_search(args):
    """Search indexed files with auto-sync."""
    if not SEARCH_DIR.exists():
        print(json.dumps({
            "error": "Search directory not found",
            "hint": "Run './ai-grep setup' first to initialize"
        }))
        return 1

    # Initialize DB if needed
    if not DB_PATH.exists():
        init_db(DB_PATH)

    if not args.query:
        print(json.dumps({"error": "Query required"}))
        return 1

    # Auto-sync: check if DB is stale and reindex if needed
    if is_stale(DB_PATH, threshold_minutes=5):
        print("Updating index... (detecting changes)", file=sys.stderr)
        try:
            # Load exclude patterns from config
            exclude_patterns = []
            if CONFIG_PATH.exists():
                config = json.loads(CONFIG_PATH.read_text())
                exclude_patterns = config.get("exclude_rules", [])

            result = index_files(
                Path.cwd(),
                DB_PATH,
                exclude_patterns=exclude_patterns,
                verbose=False
            )

            if result.get("errors"):
                print(f"Index warnings: {result['errors']}", file=sys.stderr)
        except Exception as e:
            print(f"Index update failed: {e}", file=sys.stderr)
            # Continue with search anyway - DB is still usable

    # Perform search
    try:
        output = search_combined(
            Path.cwd(),
            DB_PATH,
            args.query,
            limit=args.limit,
        )

        print(json.dumps(output, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_index(args):
    """Manually index or re-index all files."""
    if not SEARCH_DIR.exists():
        print(json.dumps({
            "error": "Search directory not found",
            "hint": "Run './ai-grep setup' first"
        }))
        return 1

    # Initialize DB if needed
    if not DB_PATH.exists():
        init_db(DB_PATH)

    print(f"Indexing {Path.cwd()}...", file=sys.stderr)

    try:
        # Load exclude patterns
        exclude_patterns = []
        if CONFIG_PATH.exists():
            config = json.loads(CONFIG_PATH.read_text())
            exclude_patterns = config.get("exclude_rules", [])

        result = index_files(
            Path.cwd(),
            DB_PATH,
            exclude_patterns=exclude_patterns,
            verbose=args.verbose,
        )

        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_validate(args):
    """Check database integrity."""
    if not DB_PATH.exists():
        print(json.dumps({
            "valid": False,
            "error": "Database file not found",
            "hint": "Run './ai-grep setup' first"
        }))
        return 1

    try:
        setup_result = validate_setup(Path.cwd())

        valid = (
            setup_result.get("valid") and
            DB_PATH.exists() and
            CONFIG_PATH.exists()
        )

        print(json.dumps({
            "valid": valid,
            "details": setup_result,
        }, indent=2))

        return 0 if valid else 1
    except Exception as e:
        print(json.dumps({
            "valid": False,
            "error": str(e)
        }))
        return 1


def cmd_config(args):
    """Show current configuration."""
    if not CONFIG_PATH.exists():
        print(json.dumps({
            "error": "Configuration not found",
            "hint": "Run './ai-grep setup' first"
        }))
        return 1

    try:
        config = json.loads(CONFIG_PATH.read_text())
        print(json.dumps(config, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_get(args):
    """Retrieve full content of an indexed file."""
    if not DB_PATH.exists():
        print(json.dumps({
            "error": "Database not initialized",
            "hint": "Run './ai-grep setup' first"
        }))
        return 1

    if not args.filepath:
        print(json.dumps({"error": "Filepath required"}))
        return 1

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Try exact match first, then pattern match
        cursor.execute(
            "SELECT file_path, filename, file_type, content, file_size, indexed_at FROM files WHERE file_path = ?",
            (args.filepath,)
        )
        row = cursor.fetchone()

        # If not found, try matching by filename or partial path
        if not row:
            cursor.execute(
                "SELECT file_path, filename, file_type, content, file_size, indexed_at FROM files WHERE file_path LIKE ? OR filename = ?",
                (f"%{args.filepath}%", args.filepath)
            )
            row = cursor.fetchone()

        conn.close()

        if not row:
            print(json.dumps({
                "error": f"File not found in index: {args.filepath}",
                "hint": "Use './ai-grep list' to see indexed files"
            }))
            return 1

        content = row["content"]
        lines = content.split('\n')

        # Handle --lines flag (e.g., "10-20" or "10")
        if args.lines:
            try:
                if '-' in args.lines:
                    start, end = map(int, args.lines.split('-'))
                else:
                    start = int(args.lines)
                    end = start + 10  # Default to 10 lines if only start given
                # Convert to 0-indexed
                start = max(0, start - 1)
                end = min(len(lines), end)
                lines = lines[start:end]
                content = '\n'.join(lines)
            except ValueError:
                print(json.dumps({"error": f"Invalid --lines format: {args.lines}. Use N or N-M"}))
                return 1

        # Raw output mode - just print content
        if args.raw:
            print(content)
            return 0

        # JSON output with metadata
        result = {
            "filepath": row["file_path"],
            "filename": row["filename"],
            "file_type": row["file_type"],
            "file_size": row["file_size"],
            "line_count": len(row["content"].split('\n')),
            "indexed_at": row["indexed_at"],
            "content": content,
        }
        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_bundle(args):
    """Retrieve multiple indexed files at once."""
    if not DB_PATH.exists():
        print(json.dumps({
            "error": "Database not initialized",
            "hint": "Run './ai-grep setup' first"
        }))
        return 1

    if not args.filepaths:
        print(json.dumps({"error": "Filepaths required (comma-separated)"}))
        return 1

    # Parse comma-separated paths
    paths = [p.strip() for p in args.filepaths.split(',') if p.strip()]

    if not paths:
        print(json.dumps({"error": "No valid filepaths provided"}))
        return 1

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        results = []
        not_found = []

        for filepath in paths:
            # Try exact match first
            cursor.execute(
                "SELECT file_path, filename, file_type, content, file_size, indexed_at FROM files WHERE file_path = ?",
                (filepath,)
            )
            row = cursor.fetchone()

            # Try pattern match
            if not row:
                cursor.execute(
                    "SELECT file_path, filename, file_type, content, file_size, indexed_at FROM files WHERE file_path LIKE ? OR filename = ?",
                    (f"%{filepath}%", filepath)
                )
                row = cursor.fetchone()

            if row:
                results.append({
                    "filepath": row["file_path"],
                    "filename": row["filename"],
                    "file_type": row["file_type"],
                    "file_size": row["file_size"],
                    "line_count": len(row["content"].split('\n')),
                    "indexed_at": row["indexed_at"],
                    "content": row["content"],
                })
            else:
                not_found.append(filepath)

        conn.close()

        # Raw output mode - concatenate contents with separator
        if args.raw:
            separator = args.separator or "\n\n---\n\n"
            output_parts = []
            for r in results:
                if args.separator:
                    output_parts.append(r["content"])
                else:
                    output_parts.append(f"### {r['filepath']}\n\n{r['content']}")
            print(separator.join(output_parts))
            return 0

        # JSON output
        output = {
            "requested": len(paths),
            "found": len(results),
            "not_found": not_found if not_found else None,
            "files": results,
        }
        print(json.dumps(output, indent=2))
        return 0

    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_context(args):
    """Get context lines around a specific line in a file."""
    if not DB_PATH.exists():
        print(json.dumps({
            "error": "Database not initialized",
            "hint": "Run './ai-grep setup' first"
        }))
        return 1

    if not args.filepath:
        print(json.dumps({"error": "Filepath required"}))
        return 1

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Find the file
        cursor.execute(
            "SELECT file_path, content FROM files WHERE file_path = ?",
            (args.filepath,)
        )
        row = cursor.fetchone()

        if not row:
            cursor.execute(
                "SELECT file_path, content FROM files WHERE file_path LIKE ? OR filename = ?",
                (f"%{args.filepath}%", args.filepath)
            )
            row = cursor.fetchone()

        conn.close()

        if not row:
            print(json.dumps({
                "error": f"File not found: {args.filepath}",
                "hint": "Use './ai-grep list' to see indexed files"
            }))
            return 1

        lines = row["content"].split('\n')
        total_lines = len(lines)
        target_line = args.line or 1
        around = args.around or 5

        # Calculate range (1-indexed input, convert to 0-indexed)
        start = max(0, target_line - 1 - around)
        end = min(total_lines, target_line - 1 + around + 1)

        context_lines = []
        for i in range(start, end):
            line_num = i + 1  # Back to 1-indexed for display
            marker = ">>>" if line_num == target_line else "   "
            context_lines.append({
                "line_num": line_num,
                "marker": marker.strip() if marker.strip() else None,
                "content": lines[i]
            })

        # Raw output - numbered lines
        if args.raw:
            for item in context_lines:
                marker = ">>> " if item["marker"] else "    "
                print(f"{marker}{item['line_num']:4d}: {item['content']}")
            return 0

        # JSON output
        result = {
            "filepath": row["file_path"],
            "target_line": target_line,
            "around": around,
            "total_lines": total_lines,
            "range": {"start": start + 1, "end": end},
            "lines": context_lines,
        }
        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_list(args):
    """List all indexed files."""
    if not DB_PATH.exists():
        print(json.dumps({
            "error": "Database not initialized",
            "hint": "Run './ai-grep setup' first"
        }))
        return 1

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Build query based on filters
        query = "SELECT file_path, filename, file_type, file_size, indexed_at FROM files WHERE 1=1"
        params = []

        if args.type:
            query += " AND file_type = ?"
            params.append(args.type)

        if args.pattern:
            query += " AND (file_path LIKE ? OR filename LIKE ?)"
            params.extend([f"%{args.pattern}%", f"%{args.pattern}%"])

        # Order by most recent first
        query += " ORDER BY indexed_at DESC"

        if args.recent:
            query += " LIMIT ?"
            params.append(args.recent)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        files = []
        for row in rows:
            files.append({
                "filepath": row["file_path"],
                "filename": row["filename"],
                "file_type": row["file_type"],
                "file_size": row["file_size"],
                "indexed_at": row["indexed_at"],
            })

        # Raw output - just paths
        if args.raw:
            for f in files:
                print(f["filepath"])
            return 0

        result = {
            "total": len(files),
            "filters": {
                "type": args.type,
                "pattern": args.pattern,
                "recent": args.recent,
            },
            "files": files,
        }
        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


# ============================================================================
# Analysis Commands (Track 1)
# ============================================================================


def cmd_stats_handler(args):
    """Handle stats command."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_stats(db_path)
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


def cmd_timeline_handler(args):
    """Handle timeline command."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_timeline(db_path, days=args.days, limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


def cmd_tags_handler(args):
    """Handle tags command."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_tags(db_path)
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


def cmd_outline_handler(args):
    """Handle outline command."""
    if not args.filepath:
        print(json.dumps({"error": "filepath required. Usage: ./ai-grep outline <filepath>"}))
        return 1
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_outline(db_path, args.filepath)
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


def cmd_toc_handler(args):
    """Handle toc command."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_toc(db_path, file_type=args.type)
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


# ============================================================================
# Content Analysis Commands (Track 2)
# ============================================================================


def cmd_related_handler(args):
    """Handle related command - find similar files."""
    if not args.filepath:
        print(json.dumps({"error": "filepath required. Usage: ./ai-grep related <filepath>"}))
        return 1
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    try:
        result = cmd_related(db_path, args.filepath, top=args.top)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_duplicates_handler(args):
    """Handle duplicates command."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    try:
        result = cmd_duplicates(db_path)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_links_handler(args):
    """Handle links command - extract and validate internal links."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    try:
        result = cmd_links(db_path)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_refs_handler(args):
    """Handle refs command - find symbol references."""
    if not args.symbol:
        print(json.dumps({"error": "symbol required. Usage: ./ai-grep refs <symbol>"}))
        return 1
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    try:
        result = cmd_refs(db_path, args.symbol, context=args.context)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


# ============================================================================
# Multi-Source Commands (Track 3)
# ============================================================================


def cmd_mount_handler(args):
    """Handle mount command - add external directory."""
    if not args.path or not args.alias:
        print(json.dumps({"error": "path and alias required. Usage: ./ai-grep mount <path> <alias>"}))
        return 1
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_mount(db_path, args.path, args.alias)
    print(result["message"])
    return 0 if result["success"] else 1


def cmd_sources_handler(args):
    """Handle sources command - list mounted directories."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_sources(db_path)
    if result["success"]:
        if not result["sources"]:
            print("No sources mounted. Use './ai-grep mount <path> <alias>' to add one.")
        else:
            print(f"{'Alias':<15} {'Path':<50} {'Files':>8} {'Status':>8}")
            print("-" * 85)
            for src in result["sources"]:
                status = "OK" if src["exists"] else "MISSING"
                print(f"{src['alias']:<15} {src['absolute_path']:<50} {src['file_count']:>8} {status:>8}")
        return 0
    else:
        print(f"Error: {result['error']}")
        return 1


def cmd_unmount_handler(args):
    """Handle unmount command - remove directory from index."""
    if not args.alias:
        print(json.dumps({"error": "alias required. Usage: ./ai-grep unmount <alias>"}))
        return 1
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_unmount(db_path, args.alias)
    print(result["message"])
    return 0 if result["success"] else 1


# ============================================================================
# Export & Integration Commands (Track 4)
# ============================================================================


def cmd_export_handler(args):
    """Handle export command - export search results."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    root_path = Path.cwd()

    # First run a search to get results
    if not args.query:
        print(json.dumps({"error": "query required. Usage: ./ai-grep export <query> [--format json|csv|md]"}))
        return 1

    try:
        search_results = search_combined(root_path, db_path, args.query, limit=args.limit)
        results = search_results.get("results", [])

        export_result = cmd_export(
            results=results,
            format=args.format,
            output=args.output,
            query=args.query
        )

        if export_result["success"]:
            if args.output:
                print(f"Exported {len(results)} results to {export_result['output_path']}")
            else:
                print(export_result["content"])
            return 0
        else:
            print(json.dumps({"error": export_result["error"]}))
            return 1
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def cmd_clip_handler(args):
    """Handle clip command - copy file to clipboard."""
    if not args.filepath:
        print(json.dumps({"error": "filepath required. Usage: ./ai-grep clip <filepath>"}))
        return 1
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_clip(args.filepath, db_path)
    if result["success"]:
        print(f"Copied {result['char_count']:,} characters to clipboard ({result['method']})")
        return 0
    else:
        print(f"Error: {result['error']}")
        return 1


def cmd_open_handler(args):
    """Handle open command - open file in editor."""
    if not args.filepath:
        print(json.dumps({"error": "filepath required. Usage: ./ai-grep open <filepath> [--line N]"}))
        return 1
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    root_path = Path.cwd()
    result = cmd_open(args.filepath, db_path, line=args.line, root_path=root_path)
    if result["success"]:
        line_info = f" at line {result['line']}" if result['line'] else ""
        print(f"Opened {result['filepath']} in {result['editor']}{line_info}")
        return 0
    else:
        print(f"Error: {result['error']}")
        return 1


def cmd_history_handler(args):
    """Handle history command - show/clear query history."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    result = cmd_history(db_path, limit=args.limit, clear=args.clear)
    if result["success"]:
        if args.clear:
            print(f"Cleared {result['count']} queries from history")
        elif not result["queries"]:
            print("No search history yet.")
        else:
            print(f"Recent {len(result['queries'])} queries:\n")
            for q in result["queries"]:
                print(f"  [{q['executed_at'][:19]}] {q['query']} ({q['result_count']} results)")
        return 0
    else:
        print(f"Error: {result['error']}")
        return 1


# ============================================================================
# Search Enhancement Commands (Track 5)
# ============================================================================


def cmd_diff_handler(args):
    """Handle diff command - show changes since last index."""
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    root_path = Path.cwd()
    result = cmd_diff(root_path, db_path)
    if result.get("error"):
        print(json.dumps({"error": result["error"]}))
        return 1

    # Human-readable summary
    counts = result["counts"]
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Changes since last index ({result.get('last_indexed_at', 'never')}):\n")
        print(f"  New files:      {counts['new']:>5}")
        print(f"  Modified:       {counts['modified']:>5}")
        print(f"  Deleted:        {counts['deleted']:>5}")
        print(f"  Unchanged:      {counts['unchanged']:>5}")
        print(f"  ─────────────────────")
        print(f"  Total indexed:  {counts['total_indexed']:>5}")
        print(f"  Total current:  {counts['total_current']:>5}")

        if result["new_files"] and len(result["new_files"]) <= 20:
            print(f"\nNew files:")
            for f in result["new_files"][:20]:
                print(f"  + {f}")

        if result["modified_files"] and len(result["modified_files"]) <= 20:
            print(f"\nModified files:")
            for f in result["modified_files"][:20]:
                print(f"  ~ {f}")

        if result["deleted_files"] and len(result["deleted_files"]) <= 20:
            print(f"\nDeleted files:")
            for f in result["deleted_files"][:20]:
                print(f"  - {f}")

    return 0


def cmd_grep_context_handler(args):
    """Handle grep-context command - pattern search with rich context."""
    if not args.pattern:
        print(json.dumps({"error": "pattern required. Usage: ./ai-grep grep-context <pattern>"}))
        return 1
    root_path = Path.cwd()
    result = cmd_grep_context(root_path, args.pattern, context=args.context, limit=args.limit)
    if result.get("error"):
        print(json.dumps({"error": result["error"]}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_relevant_handler(args):
    """Handle relevant command - pre-ranked file list for a query."""
    if not args.query:
        print(json.dumps({"error": "query required. Usage: ./ai-grep relevant <query>"}))
        return 1
    db_path = Path.cwd() / "SEARCH" / ".vault.db"
    root_path = Path.cwd()
    result = cmd_relevant(root_path, db_path, args.query, top=args.top)
    print(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="AI-grep - Token-Efficient Directory Search for AI Assistants",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help="Verbose output"
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # setup
    p_setup = subparsers.add_parser('setup', help='Initialize database, then index all files')
    p_setup.add_argument('--no-index', action='store_true', dest='no_index',
                         help='Skip auto-indexing after setup')

    # status
    p_status = subparsers.add_parser('status', help='Show index info')

    # search
    p_search = subparsers.add_parser('search', help='Search indexed files (auto-syncs)')
    p_search.add_argument('query', nargs='?', help='Search query')
    p_search.add_argument('--limit', type=int, default=50, help='Max results')

    # index
    p_index = subparsers.add_parser('index', help='Manually index all files')
    p_index.add_argument('--force', action='store_true', help='Force re-index all files')

    # validate
    p_validate = subparsers.add_parser('validate', help='Check database integrity')

    # config
    p_config = subparsers.add_parser('config', help='Show configuration')

    # get - retrieve full file content
    p_get = subparsers.add_parser('get', help='Get full content of an indexed file')
    p_get.add_argument('filepath', nargs='?', help='File path (exact or partial match)')
    p_get.add_argument('--raw', action='store_true', help='Output raw content only (no JSON)')
    p_get.add_argument('--lines', type=str, help='Line range: N or N-M (e.g., 10 or 10-20)')

    # bundle - retrieve multiple files
    p_bundle = subparsers.add_parser('bundle', help='Get multiple files at once')
    p_bundle.add_argument('filepaths', nargs='?', help='Comma-separated file paths')
    p_bundle.add_argument('--raw', action='store_true', help='Output raw content only')
    p_bundle.add_argument('--separator', type=str, help='Separator between files (default: markdown header)')

    # context - get lines around a specific line
    p_context = subparsers.add_parser('context', help='Get context around a specific line')
    p_context.add_argument('filepath', nargs='?', help='File path')
    p_context.add_argument('--line', type=int, default=1, help='Target line number (default: 1)')
    p_context.add_argument('--around', type=int, default=5, help='Lines before/after (default: 5)')
    p_context.add_argument('--raw', action='store_true', help='Output numbered lines only')

    # list - list indexed files
    p_list = subparsers.add_parser('list', help='List indexed files')
    p_list.add_argument('--type', type=str, help='Filter by file type (e.g., markdown, python)')
    p_list.add_argument('--pattern', type=str, help='Filter by path/filename pattern')
    p_list.add_argument('--recent', type=int, help='Show only N most recent files')
    p_list.add_argument('--raw', action='store_true', help='Output paths only (no JSON)')

    # ========================================================================
    # ANALYSIS COMMANDS (Track 1)
    # ========================================================================

    # stats - codebase statistics
    subparsers.add_parser('stats', help='Show codebase statistics (files, sizes, types)')

    # timeline - files by date
    p_timeline = subparsers.add_parser('timeline', help='Show files by modification date')
    p_timeline.add_argument('--days', type=int, default=30, help='Number of days to look back (default: 30)')
    p_timeline.add_argument('--limit', type=int, default=50, help='Max files to show (default: 50)')

    # tags - extract tags and markers
    subparsers.add_parser('tags', help='Extract frontmatter, hashtags, TODO markers')

    # outline - file structure
    p_outline = subparsers.add_parser('outline', help='Show file structure (headers, functions)')
    p_outline.add_argument('filepath', nargs='?', help='File to analyze')

    # toc - table of contents
    p_toc = subparsers.add_parser('toc', help='Table of contents for all files')
    p_toc.add_argument('--type', type=str, help='Filter by file type')

    # ========================================================================
    # CONTENT ANALYSIS COMMANDS (Track 2)
    # ========================================================================

    # related - find similar files
    p_related = subparsers.add_parser('related', help='Find similar files using TF-IDF')
    p_related.add_argument('filepath', nargs='?', help='Target file to find similar files for')
    p_related.add_argument('--top', type=int, default=5, help='Number of similar files (default: 5)')

    # duplicates - find duplicate content
    subparsers.add_parser('duplicates', help='Find duplicate/near-duplicate files')

    # links - extract internal links
    subparsers.add_parser('links', help='Extract and validate internal links')

    # refs - find symbol references
    p_refs = subparsers.add_parser('refs', help='Find all references to a symbol')
    p_refs.add_argument('symbol', nargs='?', help='Symbol to search for')
    p_refs.add_argument('--context', type=int, default=2, help='Context lines (default: 2)')

    # ========================================================================
    # MULTI-SOURCE COMMANDS (Track 3)
    # ========================================================================

    # mount - add external directory
    p_mount = subparsers.add_parser('mount', help='Add external directory to index')
    p_mount.add_argument('path', nargs='?', help='Directory path to mount')
    p_mount.add_argument('alias', nargs='?', help='Alias for this source')

    # sources - list mounted directories
    subparsers.add_parser('sources', help='List all mounted directories')

    # unmount - remove directory
    p_unmount = subparsers.add_parser('unmount', help='Remove directory from index')
    p_unmount.add_argument('alias', nargs='?', help='Alias of source to unmount')

    # ========================================================================
    # EXPORT & INTEGRATION COMMANDS (Track 4)
    # ========================================================================

    # export - export search results
    p_export = subparsers.add_parser('export', help='Export search results to file')
    p_export.add_argument('query', nargs='?', help='Search query')
    p_export.add_argument('--format', choices=['json', 'csv', 'md'], default='json', help='Output format')
    p_export.add_argument('--output', type=str, help='Output file path (default: stdout)')
    p_export.add_argument('--limit', type=int, default=50, help='Max results to export')

    # clip - copy file to clipboard
    p_clip = subparsers.add_parser('clip', help='Copy file content to clipboard')
    p_clip.add_argument('filepath', nargs='?', help='File to copy')

    # open - open in editor
    p_open = subparsers.add_parser('open', help='Open file in editor')
    p_open.add_argument('filepath', nargs='?', help='File to open')
    p_open.add_argument('--line', type=int, help='Line number to jump to')

    # history - query history
    p_history = subparsers.add_parser('history', help='Show search query history')
    p_history.add_argument('--limit', type=int, default=20, help='Max queries to show')
    p_history.add_argument('--clear', action='store_true', help='Clear history')

    # ========================================================================
    # SEARCH ENHANCEMENT COMMANDS (Track 5)
    # ========================================================================

    # diff - changes since last index
    p_diff = subparsers.add_parser('diff', help='Show changes since last index')
    p_diff.add_argument('--json', action='store_true', help='Output as JSON')

    # grep-context - pattern search with rich context
    p_grep = subparsers.add_parser('grep-context', help='Pattern search with surrounding context')
    p_grep.add_argument('pattern', nargs='?', help='Search pattern (regex)')
    p_grep.add_argument('--context', type=int, default=5, help='Context lines (default: 5)')
    p_grep.add_argument('--limit', type=int, default=50, help='Max matches (default: 50)')

    # relevant - pre-ranked file list
    p_relevant = subparsers.add_parser('relevant', help='Get top relevant files for a query')
    p_relevant.add_argument('query', nargs='?', help='Search query')
    p_relevant.add_argument('--top', type=int, default=5, help='Number of files (default: 5)')

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    # Dispatch to command handler
    commands = {
        # Original commands
        'setup': cmd_setup,
        'status': cmd_status,
        'search': cmd_search,
        'index': cmd_index,
        'validate': cmd_validate,
        'config': cmd_config,
        'get': cmd_get,
        'bundle': cmd_bundle,
        'context': cmd_context,
        'list': cmd_list,
        # Analysis commands (Track 1)
        'stats': cmd_stats_handler,
        'timeline': cmd_timeline_handler,
        'tags': cmd_tags_handler,
        'outline': cmd_outline_handler,
        'toc': cmd_toc_handler,
        # Content analysis commands (Track 2)
        'related': cmd_related_handler,
        'duplicates': cmd_duplicates_handler,
        'links': cmd_links_handler,
        'refs': cmd_refs_handler,
        # Multi-source commands (Track 3)
        'mount': cmd_mount_handler,
        'sources': cmd_sources_handler,
        'unmount': cmd_unmount_handler,
        # Export commands (Track 4)
        'export': cmd_export_handler,
        'clip': cmd_clip_handler,
        'open': cmd_open_handler,
        'history': cmd_history_handler,
        # Search enhancements (Track 5)
        'diff': cmd_diff_handler,
        'grep-context': cmd_grep_context_handler,
        'relevant': cmd_relevant_handler,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
