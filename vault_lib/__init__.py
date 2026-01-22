"""
Vault Library - Portable Directory Search Tool

This package provides indexing and search tools for any directory.
Completely self-contained - no external dependencies except ripgrep and Python packages.

Modules:
    setup       - Dependency checking and database initialization
    file_extract - Extract content from all file types
    index       - Incremental SQLite FTS5 indexing
    search      - Ripgrep and FTS5 search combined
"""

__version__ = "1.0.0"
__all__ = []
