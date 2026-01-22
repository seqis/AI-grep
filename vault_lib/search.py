"""
File Search - Search indexed files using FTS5 and ripgrep.

Provides search over indexed files with:
- FTS5 full-text search with BM25 ranking
- Ripgrep lexical search with context
- Combined search with deduplication and recency boost

Expected database schema (created by setup.py):
    CREATE TABLE files (
        file_id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT UNIQUE NOT NULL,
        filename TEXT NOT NULL,
        file_type TEXT,
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        file_size INTEGER,
        modified_at TEXT,
        indexed_at TEXT NOT NULL
    );

    CREATE VIRTUAL TABLE files_fts USING fts5(
        file_path,
        filename,
        content,
        content='files',
        content_rowid='file_id',
        tokenize='porter unicode61'
    );
"""

import json
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Constants
DEFAULT_SEARCH_LIMIT = 50
DEFAULT_CONTEXT_LINES = 3
DEFAULT_SNIPPET_CHARS = 300
RECENCY_DECAY_FACTOR = 0.01  # Score boost decay per day
DATE_DISCREPANCY_THRESHOLD_DAYS = 30  # Flag if mtime and content dates differ by this much


# =============================================================================
# Database Section Queries - Use pre-indexed file_sections table
# =============================================================================

def get_section_for_match(db_path: Path, file_id: int, line_number: int) -> Optional[dict]:
    """
    Query file_sections table to find which section contains a line.

    Args:
        db_path: Path to SQLite database
        file_id: The file_id from the files table
        line_number: 1-indexed line number of the match

    Returns:
        Dictionary with section info if found:
        {
            "section_date": "2024-01-15",
            "section_header": "#### 2024-01-15:",
            "section_type": "date_header",
            "line_start": 42,
            "line_end": 87
        }
        Returns None if no section contains this line.
    """
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT section_date, section_header, section_type, line_start, line_end
            FROM file_sections
            WHERE file_id = ? AND line_start <= ?
            ORDER BY line_start DESC
            LIMIT 1
        """, (file_id, line_number))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
        return None
    except sqlite3.Error:
        return None


def get_entry_dates_for_matches(db_path: Path, file_id: int, line_numbers: list[int]) -> list[dict]:
    """
    Get unique entry dates for all match lines in a file.

    Queries the file_sections table to find which section each match line
    belongs to, then deduplicates by date.

    Args:
        db_path: Path to SQLite database
        file_id: The file_id from the files table
        line_numbers: List of 1-indexed line numbers where matches occur

    Returns:
        List of unique section info dicts sorted by date (newest first):
        [
            {"date": "2024-01-15", "header": "#### 2024-01-15:", "line_start": 42},
            {"date": "2024-01-10", "header": "#### 2024-01-10:", "line_start": 12},
        ]
    """
    if not db_path.exists() or not line_numbers:
        return []

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # For each line number, find the containing section
        seen_dates = set()
        sections = []

        for line_num in sorted(set(line_numbers)):
            cursor.execute("""
                SELECT section_date, section_header, section_type, line_start, line_end
                FROM file_sections
                WHERE file_id = ? AND line_start <= ?
                ORDER BY line_start DESC
                LIMIT 1
            """, (file_id, line_num))
            row = cursor.fetchone()

            if row and row["section_date"]:
                date = row["section_date"]
                if date not in seen_dates:
                    seen_dates.add(date)
                    sections.append({
                        "date": date,
                        "header": row["section_header"],
                        "type": row["section_type"],
                        "line_start": row["line_start"],
                        "line_end": row["line_end"],
                    })

        conn.close()

        # Sort by date, newest first
        sections.sort(key=lambda s: s["date"], reverse=True)
        return sections

    except sqlite3.Error:
        return []


def check_file_sections_exist(db_path: Path, file_id: int) -> bool:
    """
    Check if file_sections have been indexed for a given file.

    Args:
        db_path: Path to SQLite database
        file_id: The file_id to check

    Returns:
        True if sections exist for this file, False otherwise
    """
    if not db_path.exists():
        return False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM file_sections WHERE file_id = ? LIMIT 1",
            (file_id,)
        )
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except sqlite3.Error:
        return False


def get_file_id_by_path(db_path: Path, filepath: str) -> Optional[int]:
    """
    Look up file_id from the database by filepath.

    Args:
        db_path: Path to SQLite database
        filepath: The file path to look up (can be absolute or relative)

    Returns:
        The file_id if found, None otherwise
    """
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Try exact match first
        cursor.execute(
            "SELECT file_id FROM files WHERE file_path = ?",
            (filepath,)
        )
        row = cursor.fetchone()
        if row:
            conn.close()
            return row[0]

        # For absolute paths, try to find stored paths that match the suffix
        # e.g., /home/user/project/Notes/file.md should match Notes/file.md
        filepath_path = Path(filepath)

        # Try progressively shorter path suffixes
        parts = filepath_path.parts
        for i in range(len(parts)):
            suffix = str(Path(*parts[i:]))
            cursor.execute(
                "SELECT file_id FROM files WHERE file_path = ?",
                (suffix,)
            )
            row = cursor.fetchone()
            if row:
                conn.close()
                return row[0]

        # Last resort: try matching just the filename
        cursor.execute(
            "SELECT file_id FROM files WHERE file_path LIKE ?",
            (f"%/{filepath_path.name}",)
        )
        row = cursor.fetchone()
        if row:
            conn.close()
            return row[0]

        # Also try without leading slash
        cursor.execute(
            "SELECT file_id FROM files WHERE file_path LIKE ?",
            (f"%{filepath_path.name}",)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def extract_content_dates(content: str, max_dates: int = 10) -> list[str]:
    """
    Extract dates mentioned within document content.

    Looks for:
    - Markdown headers with dates: #### 2024-01-15, ## 2024-01-15:
    - Frontmatter dates: date: 2024-01-15, created: 2024-01-15
    - ISO dates inline: 2024-01-15, 2024/01/15
    - Common date formats: January 15, 2024, 15 Jan 2024

    Args:
        content: File content to scan
        max_dates: Maximum unique dates to return (default 10)

    Returns:
        List of date strings in ISO format (YYYY-MM-DD), sorted newest first
    """
    if not content:
        return []

    found_dates = set()

    # Pattern 1: ISO dates (YYYY-MM-DD or YYYY/MM/DD)
    iso_pattern = r'\b(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b'
    for match in re.finditer(iso_pattern, content):
        year, month, day = match.groups()
        try:
            # Validate it's a real date
            datetime(int(year), int(month), int(day))
            found_dates.add(f"{year}-{month}-{day}")
        except ValueError:
            continue

    # Pattern 2: Month name dates (January 15, 2024 or 15 January 2024 or Jan 15, 2024)
    month_names = {
        'january': '01', 'february': '02', 'march': '03', 'april': '04',
        'may': '05', 'june': '06', 'july': '07', 'august': '08',
        'september': '09', 'october': '10', 'november': '11', 'december': '12',
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
        'jun': '06', 'jul': '07', 'aug': '08', 'sep': '09',
        'oct': '10', 'nov': '11', 'dec': '12'
    }

    # Month Day, Year format (January 15, 2024)
    month_day_year = r'\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b'
    for match in re.finditer(month_day_year, content, re.IGNORECASE):
        month_str, day, year = match.groups()
        month = month_names.get(month_str.lower())
        if month:
            try:
                datetime(int(year), int(month), int(day))
                found_dates.add(f"{year}-{month}-{day.zfill(2)}")
            except ValueError:
                continue

    # Day Month Year format (15 January 2024)
    day_month_year = r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?,?\s+(20\d{2})\b'
    for match in re.finditer(day_month_year, content, re.IGNORECASE):
        day, month_str, year = match.groups()
        month = month_names.get(month_str.lower())
        if month:
            try:
                datetime(int(year), int(month), int(day))
                found_dates.add(f"{year}-{month}-{day.zfill(2)}")
            except ValueError:
                continue

    # Sort by date (newest first) and limit
    sorted_dates = sorted(found_dates, reverse=True)
    return sorted_dates[:max_dates]


def calculate_date_discrepancy(
    mtime: float,
    content_dates: list[str]
) -> Optional[dict]:
    """
    Check if there's a significant discrepancy between file modification time
    and dates mentioned in the content.

    Args:
        mtime: File modification timestamp (Unix)
        content_dates: List of dates found in content (YYYY-MM-DD format)

    Returns:
        Dictionary with discrepancy info if significant, None otherwise
        {
            "has_discrepancy": True,
            "mtime_date": "2024-01-15",
            "newest_content_date": "2022-05-20",
            "oldest_content_date": "2022-01-10",
            "days_difference": 605,
            "note": "File modified recently but content dates are from 2022"
        }
    """
    if not content_dates or not mtime:
        return None

    mtime_date = datetime.fromtimestamp(mtime).date()
    mtime_str = mtime_date.isoformat()

    # Parse content dates
    parsed_dates = []
    for date_str in content_dates:
        try:
            parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
            parsed_dates.append(parsed)
        except ValueError:
            continue

    if not parsed_dates:
        return None

    newest_content = max(parsed_dates)
    oldest_content = min(parsed_dates)

    # Calculate difference from mtime to newest content date
    days_diff = abs((mtime_date - newest_content).days)

    if days_diff < DATE_DISCREPANCY_THRESHOLD_DAYS:
        return None

    # Determine the nature of the discrepancy
    if mtime_date > newest_content:
        note = f"File modified ({mtime_str}) after newest content date ({newest_content.year})"
    else:
        note = f"Content dates ({newest_content.isoformat()}) are newer than file modification ({mtime_str})"

    return {
        "has_discrepancy": True,
        "file_modified_date": mtime_str,
        "newest_content_date": newest_content.isoformat(),
        "oldest_content_date": oldest_content.isoformat(),
        "days_difference": days_diff,
        "note": note
    }


# =============================================================================
# Context Extraction Utilities - Find dates and sections near matches
# =============================================================================

# Date patterns to search for (supports ISO YYYY-MM-DD and US MM-DD-YYYY)
DATE_PATTERNS = [
    # ISO format: 2024-01-15 or 2024/01/15 (often in headers like #### 2024-01-15:)
    (r'(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])', 'ymd'),
    # US format: 01-15-2024 or 01/15/2024
    (r'(0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])[-/](20\d{2})', 'mdy'),
]

# Maximum lines to look backward for context
DEFAULT_CONTEXT_LOOKBACK = 50


def find_nearest_date_above(
    lines: list[str],
    match_line: int,
    max_lookback: int = DEFAULT_CONTEXT_LOOKBACK
) -> Optional[dict]:
    """
    Walk backward from match line to find the nearest date.

    Args:
        lines: List of file lines (0-indexed)
        match_line: 1-indexed line number of the match
        max_lookback: Maximum lines to search backward (default 50)

    Returns:
        Dictionary with date info if found:
        {
            "date": "2024-01-15",  # Normalized to ISO format
            "line_number": 42,     # 1-indexed line where date was found
            "line_text": "#### 2024-01-15:",  # The actual line
            "distance": 5          # Lines between date and match
        }
        Returns None if no date found within lookback range.
    """
    if not lines or match_line < 1:
        return None

    # Convert to 0-indexed
    start_idx = match_line - 1
    if start_idx >= len(lines):
        start_idx = len(lines) - 1

    # Calculate how far back to search
    end_idx = max(0, start_idx - max_lookback)

    # Walk backward from match line
    for idx in range(start_idx, end_idx - 1, -1):
        line = lines[idx]

        # Try each date pattern
        for pattern, fmt in DATE_PATTERNS:
            match = re.search(pattern, line)
            if match:
                groups = match.groups()
                try:
                    if fmt == 'ymd':
                        year, month, day = groups
                    else:  # mdy
                        month, day, year = groups

                    # Validate it's a real date
                    datetime(int(year), int(month), int(day))

                    return {
                        "date": f"{year}-{month}-{day}",
                        "line_number": idx + 1,  # Convert back to 1-indexed
                        "line_text": line.strip(),
                        "distance": start_idx - idx
                    }
                except ValueError:
                    continue  # Invalid date, keep looking

    return None


def find_section_start(
    lines: list[str],
    match_line: int,
    file_type: str = "md",
    max_lookback: int = DEFAULT_CONTEXT_LOOKBACK
) -> dict:
    """
    Find the start of the section containing the match.

    For markdown: looks for header lines (^#+\s)
    For other files: looks for blank lines or significant separators

    Args:
        lines: List of file lines (0-indexed)
        match_line: 1-indexed line number of the match
        file_type: File extension (md, txt, py, etc.)
        max_lookback: Maximum lines to search backward

    Returns:
        Dictionary with section info:
        {
            "line_number": 37,        # 1-indexed start of section
            "line_text": "#### 2024-01-15:",  # The section header/start
            "is_header": True,        # True if it's a markdown header
            "distance": 8             # Lines between section start and match
        }
    """
    if not lines or match_line < 1:
        return {"line_number": 1, "line_text": "", "is_header": False, "distance": match_line - 1}

    # Convert to 0-indexed
    start_idx = match_line - 1
    if start_idx >= len(lines):
        start_idx = len(lines) - 1

    end_idx = max(0, start_idx - max_lookback)

    # Markdown header pattern
    md_header_pattern = re.compile(r'^#{1,6}\s+')

    # For markdown files, look for headers
    if file_type in ('md', 'markdown'):
        for idx in range(start_idx, end_idx - 1, -1):
            line = lines[idx]
            if md_header_pattern.match(line):
                return {
                    "line_number": idx + 1,
                    "line_text": line.strip(),
                    "is_header": True,
                    "distance": start_idx - idx
                }

    # For all files (including md as fallback), look for blank line separators
    for idx in range(start_idx, end_idx - 1, -1):
        line = lines[idx]
        # Blank line or separator line (---, ===, etc.)
        if not line.strip() or re.match(r'^[-=]{3,}\s*$', line):
            # Return the line AFTER the blank (start of section)
            section_start = idx + 1
            if section_start <= start_idx:
                return {
                    "line_number": section_start + 1,  # 1-indexed
                    "line_text": lines[section_start].strip() if section_start < len(lines) else "",
                    "is_header": False,
                    "distance": start_idx - section_start
                }

    # No section boundary found, return start of lookback range
    return {
        "line_number": end_idx + 1,
        "line_text": lines[end_idx].strip() if end_idx < len(lines) else "",
        "is_header": False,
        "distance": start_idx - end_idx
    }


def extract_section_context(
    lines: list[str],
    section_start_line: int,
    match_line: int,
    lines_after: int = 5,
    max_section_lines: int = 30
) -> str:
    """
    Extract the section context from section start through match and a bit after.

    Args:
        lines: List of file lines (0-indexed)
        section_start_line: 1-indexed line where section starts
        match_line: 1-indexed line of the match
        lines_after: Number of lines to include after match (default 5)
        max_section_lines: Maximum total lines to return (default 30)

    Returns:
        String containing the section context with line numbers
    """
    if not lines:
        return ""

    # Convert to 0-indexed
    start_idx = max(0, section_start_line - 1)
    match_idx = match_line - 1
    end_idx = min(len(lines), match_idx + lines_after + 1)

    # Limit total lines
    if end_idx - start_idx > max_section_lines:
        # Prioritize content around the match
        half = max_section_lines // 2
        start_idx = max(0, match_idx - half)
        end_idx = min(len(lines), start_idx + max_section_lines)

    # Build context with line numbers
    context_lines = []
    for idx in range(start_idx, end_idx):
        line_num = idx + 1  # 1-indexed
        marker = " >> " if idx == match_idx else "    "
        context_lines.append(f"{line_num:4d}{marker}{lines[idx].rstrip()}")

    return "\n".join(context_lines)


class SearchError(Exception):
    """Base exception for search errors."""
    pass


class DatabaseNotInitializedError(SearchError):
    """Database not initialized or missing required tables."""
    pass


class EmptyQueryError(SearchError):
    """Search query is empty or whitespace-only."""
    pass


class RipgrepNotFoundError(SearchError):
    """Ripgrep binary not found in PATH."""
    pass


@dataclass
class FileSearchResult:
    """A single file search result with snippet and metadata."""
    filepath: str
    file_type: str
    snippet: str
    line_number: int
    score: float
    source: str = "unknown"  # 'fts', 'ripgrep', or 'combined'
    mtime: Optional[float] = None
    match_count: int = 1
    line_numbers: list[int] = field(default_factory=list)
    content_dates: list[str] = field(default_factory=list)  # Dates found in content
    full_content: Optional[str] = None  # For extracting content dates and context

    # New context fields - populated from full_content
    nearest_date_above: Optional[dict] = None  # Date found above match
    section_info: Optional[dict] = None  # Section header/start info
    section_context: Optional[str] = None  # Extracted section text with line numbers

    # Database fields for using pre-indexed file_sections
    db_path: Optional[Path] = None
    file_id: Optional[int] = None

    def __post_init__(self):
        if not self.line_numbers and self.line_number > 0:
            self.line_numbers = [self.line_number]

        # Extract context from full content if available
        if self.full_content:
            lines = self.full_content.split('\n')

            # Extract content dates if not already set
            if not self.content_dates:
                self.content_dates = extract_content_dates(self.full_content)

            # Find nearest date above the match
            if not self.nearest_date_above and self.line_number > 0:
                self.nearest_date_above = find_nearest_date_above(lines, self.line_number)

            # Find section start
            if not self.section_info and self.line_number > 0:
                self.section_info = find_section_start(lines, self.line_number, self.file_type)

            # Extract section context
            if not self.section_context and self.section_info and self.line_number > 0:
                self.section_context = extract_section_context(
                    lines,
                    self.section_info["line_number"],
                    self.line_number
                )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON output."""
        result = {
            "filepath": self.filepath,
            "file_type": self.file_type,
            "snippet": self.snippet,
            "line_number": self.line_number,
            "score": round(self.score, 4),
            "source": self.source,
            "match_count": self.match_count,
        }
        if self.mtime:
            result["mtime"] = self.mtime
            # Renamed from age_days to file_modified_days_ago for clarity
            result["file_modified_days_ago"] = int((time.time() - self.mtime) / 86400)

        # Try to use pre-indexed file_sections from DB if available
        use_db_sections = (
            self.db_path is not None and
            self.file_id is not None and
            check_file_sections_exist(self.db_path, self.file_id)
        )

        if use_db_sections:
            # Get all entry dates for matches using DB sections
            entry_dates = get_entry_dates_for_matches(
                self.db_path, self.file_id, self.line_numbers
            )

            if entry_dates:
                # Add entry_dates list (all unique dates where matches occur)
                result["entry_dates"] = entry_dates

                # Add earliest and latest entries for convenience
                if len(entry_dates) == 1:
                    result["earliest_entry"] = entry_dates[0]["date"]
                    result["latest_entry"] = entry_dates[0]["date"]
                else:
                    # entry_dates is sorted newest first
                    result["latest_entry"] = entry_dates[0]["date"]
                    result["earliest_entry"] = entry_dates[-1]["date"]

            # Get section info for primary match line using DB
            db_section = get_section_for_match(
                self.db_path, self.file_id, self.line_number
            )
            if db_section:
                result["section_info"] = {
                    "line_number": db_section["line_start"],
                    "line_text": db_section["section_header"] or "",
                    "is_header": db_section["section_type"] in ("date_header", "header"),
                    "section_date": db_section["section_date"],
                    "section_type": db_section["section_type"],
                }
                # Also add nearest_date_above for compatibility
                if db_section["section_date"]:
                    result["nearest_date_above"] = {
                        "date": db_section["section_date"],
                        "line_number": db_section["line_start"],
                        "line_text": db_section["section_header"] or "",
                        "distance": self.line_number - db_section["line_start"],
                    }
        else:
            # Fall back to walk-back functions (computed in __post_init__)
            # Add nearest date above (the key context for "when was this written")
            if self.nearest_date_above:
                result["nearest_date_above"] = self.nearest_date_above

            # Add section info
            if self.section_info:
                result["section_info"] = self.section_info

        # Add section context (the rich context for AI interpretation)
        # This is always computed from full_content, not from DB
        if self.section_context:
            result["section_context"] = self.section_context

        # Add content dates if found (legacy field, less useful than nearest_date_above)
        if self.content_dates:
            result["content_dates"] = self.content_dates

            # Check for date discrepancy and add warning if significant
            if self.mtime:
                discrepancy = calculate_date_discrepancy(self.mtime, self.content_dates)
                if discrepancy:
                    result["date_discrepancy"] = discrepancy

        if len(self.line_numbers) > 1:
            result["line_numbers"] = sorted(set(self.line_numbers))
        return result


def check_ripgrep() -> bool:
    """Check if ripgrep is installed and accessible."""
    try:
        subprocess.run(
            ["rg", "--version"],
            capture_output=True,
            check=True,
            timeout=5
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_db_initialized(db_path: Path) -> dict:
    """
    Check if database is initialized with required tables.

    Returns:
        Dictionary with:
        - initialized: bool
        - has_files_table: bool
        - has_fts_table: bool
        - file_count: int
        - error: str or None
    """
    result = {
        "initialized": False,
        "has_files_table": False,
        "has_fts_table": False,
        "file_count": 0,
        "error": None,
    }

    if not db_path.exists():
        result["error"] = f"Database not found: {db_path}"
        return result

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check for files table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
        )
        result["has_files_table"] = cursor.fetchone() is not None

        # Check for FTS table (actual table name is files_fts, not fts_files)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'"
        )
        result["has_fts_table"] = cursor.fetchone() is not None

        # Get file count if table exists
        if result["has_files_table"]:
            cursor.execute("SELECT COUNT(*) FROM files")
            result["file_count"] = cursor.fetchone()[0]

        result["initialized"] = (
            result["has_files_table"] and
            result["has_fts_table"] and
            result["file_count"] > 0
        )

        conn.close()

    except sqlite3.Error as e:
        result["error"] = f"Database error: {e}"

    return result


def get_file_type(filepath: str) -> str:
    """Extract file type/extension from filepath."""
    ext = Path(filepath).suffix.lower()
    return ext[1:] if ext else "unknown"


def extract_snippet(
    content: str,
    line_num: int,
    context_lines: int = DEFAULT_CONTEXT_LINES,
    max_chars: int = DEFAULT_SNIPPET_CHARS
) -> str:
    """
    Extract a snippet around a specific line number.

    Args:
        content: Full file content
        line_num: 1-indexed line number of the match
        context_lines: Lines to include before/after match
        max_chars: Maximum snippet length

    Returns:
        Snippet string with context
    """
    lines = content.split('\n')
    total_lines = len(lines)

    # Convert to 0-indexed
    idx = line_num - 1
    if idx < 0 or idx >= total_lines:
        return ""

    start = max(0, idx - context_lines)
    end = min(total_lines, idx + context_lines + 1)

    snippet_lines = lines[start:end]
    snippet = '\n'.join(snippet_lines)

    # Truncate if too long
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "..."

    return snippet


def validate_query(query: str) -> str:
    """
    Validate and clean search query.

    Raises:
        EmptyQueryError: If query is empty or whitespace-only

    Returns:
        Cleaned query string
    """
    cleaned = query.strip() if query else ""
    if not cleaned:
        raise EmptyQueryError("Search query cannot be empty")
    return cleaned


def search_fts(
    db_path: Path,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    recency_boost: bool = True,
) -> list[FileSearchResult]:
    """
    Search indexed files using FTS5 full-text search.

    Uses BM25 relevance ranking with optional recency boost based on
    file modification time.

    Args:
        db_path: Path to SQLite database
        query: Search query (supports FTS5 syntax: AND, OR, NOT, phrases)
        limit: Maximum results to return
        recency_boost: Apply recency boost to scores (default True)

    Returns:
        List of FileSearchResult objects sorted by score

    Raises:
        DatabaseNotInitializedError: If database or tables don't exist
        EmptyQueryError: If query is empty
    """
    query = validate_query(query)

    # Check database
    db_status = check_db_initialized(db_path)
    if not db_status["initialized"]:
        error_msg = db_status.get("error") or "Database not initialized"
        if not db_status["has_files_table"] or not db_status["has_fts_table"]:
            error_msg += ". Run the indexer first to create tables."
        raise DatabaseNotInitializedError(error_msg)

    results = []

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Build query with BM25 ranking
        # BM25 scores are negative; lower (more negative) = better match
        # filepath weight 2.0, content weight 1.0
        if recency_boost:
            # Recency boost: multiply score by recency factor
            # Use indexed_at (ISO timestamp) for recency calculation
            sql = """
                SELECT
                    f.file_id,
                    f.file_path,
                    f.content,
                    f.indexed_at,
                    snippet(files_fts, 2, '>>>', '<<<', '...', 32) as snippet,
                    bm25(files_fts, 1.0, 1.0, 1.0) as bm25_score,
                    (julianday('now') - julianday(f.indexed_at)) as age_days,
                    bm25(files_fts, 1.0, 1.0, 1.0) * (1.0 + (julianday('now') - julianday(f.indexed_at)) * ?) as final_score
                FROM files_fts
                JOIN files f ON files_fts.rowid = f.file_id
                WHERE files_fts MATCH ?
                ORDER BY final_score
                LIMIT ?
            """
            cursor.execute(sql, (RECENCY_DECAY_FACTOR, query, limit))
        else:
            sql = """
                SELECT
                    f.file_id,
                    f.file_path,
                    f.content,
                    f.indexed_at,
                    snippet(files_fts, 2, '>>>', '<<<', '...', 32) as snippet,
                    bm25(files_fts, 1.0, 1.0, 1.0) as bm25_score,
                    0 as age_days,
                    bm25(files_fts, 1.0, 1.0, 1.0) as final_score
                FROM files_fts
                JOIN files f ON files_fts.rowid = f.file_id
                WHERE files_fts MATCH ?
                ORDER BY final_score
                LIMIT ?
            """
            cursor.execute(sql, (query, limit))

        for row in cursor.fetchall():
            # Normalize BM25 score to 0-1 (higher = better)
            raw_score = row["final_score"]
            normalized_score = 1.0 / (1.0 + abs(raw_score))

            # Try to find line number from snippet context
            snippet_text = row["snippet"] or ""
            line_num = _find_line_number_in_content(
                row["content"], snippet_text
            ) if snippet_text else 1

            # Get mtime from filesystem since it's not stored in DB
            file_path = row["file_path"]
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                mtime = time.time()

            results.append(FileSearchResult(
                filepath=file_path,
                file_type=get_file_type(file_path),
                snippet=snippet_text,
                line_number=line_num,
                score=normalized_score,
                source="fts",
                mtime=mtime,
                full_content=row["content"],  # Pass content for date extraction
                db_path=db_path,  # Pass db_path for file_sections lookup
                file_id=row["file_id"],  # Pass file_id for file_sections lookup
            ))

        conn.close()

    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            raise DatabaseNotInitializedError(
                f"FTS table not found: {e}. Run indexer first."
            )
        raise

    return results


def _find_line_number_in_content(content: str, snippet: str) -> int:
    """
    Try to find the line number where snippet appears in content.

    Returns 1 if not found.
    """
    # Clean snippet markers
    clean_snippet = snippet.replace(">>>", "").replace("<<<", "").replace("...", "")
    clean_snippet = clean_snippet.strip()

    if not clean_snippet:
        return 1

    # Find first match
    lines = content.split('\n')
    for i, line in enumerate(lines, start=1):
        if clean_snippet[:50] in line:  # Match first 50 chars
            return i

    return 1


def search_ripgrep(
    root_path: Path,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    exclude_patterns: Optional[list[str]] = None,
    context_lines: int = DEFAULT_CONTEXT_LINES,
    case_insensitive: bool = True,
    db_path: Optional[Path] = None,
) -> list[FileSearchResult]:
    """
    Search files using ripgrep with context extraction.

    Args:
        root_path: Root directory to search
        query: Search pattern (supports regex)
        limit: Maximum results to return
        exclude_patterns: Glob patterns to exclude (e.g., ["*.pyc", "node_modules/*"])
        context_lines: Lines of context around matches
        case_insensitive: Case-insensitive search (default True)
        db_path: Optional path to SQLite database for file_sections lookup

    Returns:
        List of FileSearchResult objects sorted by match count

    Raises:
        RipgrepNotFoundError: If ripgrep is not installed
        EmptyQueryError: If query is empty
    """
    query = validate_query(query)

    if not check_ripgrep():
        raise RipgrepNotFoundError(
            "ripgrep (rg) not found. Install with: sudo apt install ripgrep"
        )

    if not root_path.exists():
        return []

    # Build ripgrep command
    rg_args = ["rg", "--json"]
    if case_insensitive:
        rg_args.append("-i")
    rg_args.extend(["-C", str(context_lines)])

    # Add exclude patterns
    if exclude_patterns:
        for pattern in exclude_patterns:
            rg_args.extend(["--glob", f"!{pattern}"])

    # Default exclusions for common non-text files
    default_excludes = [
        "*.pyc", "__pycache__/*", ".git/*", "node_modules/*",
        "*.jpg", "*.png", "*.gif", "*.pdf", "*.zip",
        ".vault_state/*", "*.db"
    ]
    for pattern in default_excludes:
        rg_args.extend(["--glob", f"!{pattern}"])

    rg_args.extend([query, str(root_path)])

    try:
        result = subprocess.run(
            rg_args,
            capture_output=True,
            text=True,
            timeout=30
        )
    except subprocess.TimeoutExpired:
        return []

    # Parse JSON output
    matches_by_file: dict[str, dict] = {}

    for line in result.stdout.strip().split('\n'):
        if not line:
            continue

        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get('type') == 'match':
            data = entry.get('data', {})
            path_obj = data.get('path', {})
            filepath = path_obj.get('text', '')

            if not filepath:
                continue

            line_num = data.get('line_number', 1)
            match_text = data.get('lines', {}).get('text', '').strip()

            if filepath not in matches_by_file:
                # Get file mtime
                try:
                    mtime = os.path.getmtime(filepath)
                except OSError:
                    mtime = time.time()

                matches_by_file[filepath] = {
                    'line_numbers': [],
                    'snippets': [],
                    'mtime': mtime,
                }

            matches_by_file[filepath]['line_numbers'].append(line_num)
            if match_text:
                matches_by_file[filepath]['snippets'].append(match_text)

        elif entry.get('type') == 'context':
            # Add context lines to snippet
            data = entry.get('data', {})
            path_obj = data.get('path', {})
            filepath = path_obj.get('text', '')

            if filepath and filepath in matches_by_file:
                context_text = data.get('lines', {}).get('text', '').strip()
                if context_text:
                    matches_by_file[filepath]['snippets'].append(context_text)

    # Build results
    results = []
    for filepath, data in matches_by_file.items():
        match_count = len(data['line_numbers'])
        primary_line = data['line_numbers'][0] if data['line_numbers'] else 1

        # Combine snippets
        snippets = data['snippets'][:5]  # Limit snippet parts
        combined_snippet = ' ... '.join(snippets)
        if len(combined_snippet) > DEFAULT_SNIPPET_CHARS * 2:
            combined_snippet = combined_snippet[:DEFAULT_SNIPPET_CHARS * 2] + "..."

        # Score based on match count (more matches = higher score)
        # Normalize to 0-1 range
        base_score = min(1.0, match_count / 10.0)

        # Apply recency boost
        age_days = (time.time() - data['mtime']) / 86400
        recency_factor = 1.0 / (1.0 + age_days * RECENCY_DECAY_FACTOR)
        final_score = base_score * recency_factor

        # Read file content for date extraction (if text file)
        file_content = None
        file_ext = get_file_type(filepath)
        if file_ext in ('md', 'txt', 'markdown', 'rst', 'org', 'json', 'yaml', 'yml'):
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    file_content = f.read()
            except (OSError, IOError):
                file_content = None

        # Look up file_id from database if db_path provided
        file_id = None
        if db_path is not None:
            file_id = get_file_id_by_path(db_path, filepath)

        results.append(FileSearchResult(
            filepath=filepath,
            file_type=file_ext,
            snippet=combined_snippet,
            line_number=primary_line,
            score=final_score,
            source="ripgrep",
            mtime=data['mtime'],
            match_count=match_count,
            line_numbers=data['line_numbers'],
            full_content=file_content,  # Pass content for date extraction
            db_path=db_path,  # Pass db_path for file_sections lookup
            file_id=file_id,  # Pass file_id for file_sections lookup
        ))

    # Sort by score (highest first)
    results.sort(key=lambda r: -r.score)

    return results[:limit]


def search_combined(
    root_path: Path,
    db_path: Path,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    exclude_patterns: Optional[list[str]] = None,
    recency_boost: bool = True,
) -> dict:
    """
    Combined search using both FTS5 and ripgrep for maximum recall.

    Runs both search methods, deduplicates by filepath, and merges scores.
    Results found by both methods get a bonus.

    Args:
        root_path: Root directory for ripgrep search
        db_path: Path to SQLite database for FTS search
        query: Search query
        limit: Maximum results to return
        exclude_patterns: Glob patterns to exclude from ripgrep
        recency_boost: Apply recency boost (default True)

    Returns:
        Dictionary with:
        - query: Original query
        - mode: "combined"
        - stats: Search statistics
        - results: List of result dictionaries
    """
    query = validate_query(query)

    fts_results: list[FileSearchResult] = []
    ripgrep_results: list[FileSearchResult] = []
    fts_error: Optional[str] = None
    ripgrep_error: Optional[str] = None

    # Run FTS search
    try:
        fts_results = search_fts(
            db_path, query, limit=limit * 2, recency_boost=recency_boost
        )
    except DatabaseNotInitializedError as e:
        fts_error = str(e)
    except Exception as e:
        fts_error = f"FTS search failed: {e}"

    # Run ripgrep search
    try:
        ripgrep_results = search_ripgrep(
            root_path, query, limit=limit * 2,
            exclude_patterns=exclude_patterns,
            db_path=db_path,  # Pass db_path for file_sections lookup
        )
    except RipgrepNotFoundError as e:
        ripgrep_error = str(e)
    except Exception as e:
        ripgrep_error = f"Ripgrep search failed: {e}"

    # Handle case where both failed
    if fts_error and ripgrep_error:
        return {
            "query": query,
            "mode": "combined",
            "stats": {
                "fts_count": 0,
                "ripgrep_count": 0,
                "combined_count": 0,
                "returned_count": 0,
                "fts_error": fts_error,
                "ripgrep_error": ripgrep_error,
            },
            "results": [],
        }

    # Deduplicate and merge by filepath
    # Normalize paths to handle absolute vs relative path differences
    combined: dict[str, dict] = {}

    def get_dedup_key(filepath: str) -> str:
        """Get a deduplication key for a filepath (uses filename as fallback)."""
        p = Path(filepath)
        # Use the last 2 path components as key (e.g., "Notes/Gratitude Journal.md")
        parts = p.parts
        if len(parts) >= 2:
            return str(Path(*parts[-2:]))
        return p.name

    # Add FTS results first
    for r in fts_results:
        key = get_dedup_key(r.filepath)
        combined[key] = {
            "result": r,
            "sources": ["fts"],
            "fts_score": r.score,
            "ripgrep_score": 0.0,
        }

    # Add/merge ripgrep results
    for r in ripgrep_results:
        key = get_dedup_key(r.filepath)
        if key in combined:
            # Already found by FTS - mark as found by both
            combined[key]["sources"].append("ripgrep")
            combined[key]["ripgrep_score"] = r.score

            # Merge line numbers
            existing = combined[key]["result"]
            existing.line_numbers.extend(r.line_numbers)
            existing.line_numbers = sorted(set(existing.line_numbers))

            # Transfer file_id and db_path from ripgrep result if FTS didn't have them
            if existing.file_id is None and r.file_id is not None:
                existing.file_id = r.file_id
            if existing.db_path is None and r.db_path is not None:
                existing.db_path = r.db_path

            # Extend snippet if different
            if r.snippet and r.snippet not in existing.snippet:
                if len(existing.snippet) < DEFAULT_SNIPPET_CHARS * 3:
                    existing.snippet += f" | {r.snippet[:150]}"

            # Update match count
            existing.match_count = max(existing.match_count, r.match_count)

            # Merge content_dates (keep unique, sorted newest first)
            if r.content_dates:
                all_dates = set(existing.content_dates) | set(r.content_dates)
                existing.content_dates = sorted(all_dates, reverse=True)[:10]
        else:
            # New result from ripgrep only
            combined[key] = {
                "result": r,
                "sources": ["ripgrep"],
                "fts_score": 0.0,
                "ripgrep_score": r.score,
            }

    # Calculate final scores and sort
    BOTH_SOURCES_BONUS = 0.2
    FTS_WEIGHT = 0.6
    RIPGREP_WEIGHT = 0.4

    for filepath, data in combined.items():
        both_bonus = BOTH_SOURCES_BONUS if len(data["sources"]) > 1 else 0.0
        weighted_score = (
            FTS_WEIGHT * data["fts_score"] +
            RIPGREP_WEIGHT * data["ripgrep_score"] +
            both_bonus
        )
        data["result"].score = min(1.0, weighted_score)
        data["result"].source = "combined" if len(data["sources"]) > 1 else data["sources"][0]

    # Sort by final score (highest first)
    sorted_items = sorted(
        combined.items(),
        key=lambda item: -item[1]["result"].score
    )

    # Build final results
    final_results = []
    for filepath, data in sorted_items[:limit]:
        result_dict = data["result"].to_dict()
        result_dict["sources"] = data["sources"]
        final_results.append(result_dict)

    return {
        "query": query,
        "mode": "combined",
        "stats": {
            "fts_count": len(fts_results),
            "ripgrep_count": len(ripgrep_results),
            "combined_count": len(combined),
            "returned_count": len(final_results),
            "fts_error": fts_error,
            "ripgrep_error": ripgrep_error,
        },
        "results": final_results,
    }


def search_files(
    query: str,
    root_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    mode: str = "combined",
    exclude_patterns: Optional[list[str]] = None,
    recency_boost: bool = True,
) -> dict:
    """
    High-level search function with automatic mode selection.

    Convenience wrapper that handles path defaults and mode selection.

    Args:
        query: Search query
        root_path: Root directory (defaults to cwd)
        db_path: Database path (defaults to .vault_state/vault.db)
        limit: Maximum results
        mode: Search mode - "combined", "fts", or "ripgrep"
        exclude_patterns: Patterns to exclude
        recency_boost: Apply recency boost

    Returns:
        Dictionary with search results (format depends on mode)
    """
    if root_path is None:
        root_path = Path.cwd()
    if db_path is None:
        db_path = root_path / ".vault_state" / "vault.db"

    if mode == "fts":
        results = search_fts(db_path, query, limit, recency_boost)
        return {
            "query": query,
            "mode": "fts",
            "stats": {"count": len(results), "error": None},
            "results": [r.to_dict() for r in results],
        }
    elif mode == "ripgrep":
        results = search_ripgrep(
            root_path, query, limit, exclude_patterns,
            db_path=db_path,  # Pass db_path for file_sections lookup
        )
        return {
            "query": query,
            "mode": "ripgrep",
            "stats": {"count": len(results), "error": None},
            "results": [r.to_dict() for r in results],
        }
    else:  # combined
        return search_combined(
            root_path, db_path, query, limit,
            exclude_patterns, recency_boost
        )


# ============================================================================
# Track 5: New Search Commands
# ============================================================================


def cmd_diff(root_path: Path, db_path: Path) -> dict:
    """
    Find files changed since last index.

    Compares current filesystem state against indexed state to detect:
    - New files (exist on disk but not in index)
    - Modified files (hash differs from indexed hash)
    - Deleted files (in index but not on disk)

    Args:
        root_path: Root directory to scan
        db_path: Path to SQLite database

    Returns:
        Dictionary with:
        - last_indexed_at: ISO timestamp of last index
        - new_files: List of new file paths
        - modified_files: List of modified file paths
        - deleted_files: List of deleted file paths
        - counts: Summary counts
        - error: Error message if any
    """
    # Import from index module for consistency
    from vault_lib.index import (
        calculate_file_hash,
        get_indexed_files,
        _should_exclude,
        _read_searchignore,
        DEFAULT_EXCLUDES,
    )

    result = {
        "last_indexed_at": None,
        "new_files": [],
        "modified_files": [],
        "deleted_files": [],
        "counts": {
            "new": 0,
            "modified": 0,
            "deleted": 0,
            "unchanged": 0,
            "total_indexed": 0,
            "total_current": 0,
        },
        "error": None,
    }

    # Check database exists
    if not db_path.exists():
        result["error"] = f"Database not found: {db_path}"
        return result

    # Get last indexed timestamp from manifest
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='manifest'
        """)
        if cursor.fetchone():
            cursor.execute("SELECT last_indexed_at FROM manifest WHERE id = 1")
            row = cursor.fetchone()
            if row:
                result["last_indexed_at"] = row["last_indexed_at"]

        conn.close()
    except sqlite3.Error as e:
        result["error"] = f"Database error: {e}"
        return result

    # Get indexed files
    indexed_files = get_indexed_files(db_path)
    result["counts"]["total_indexed"] = len(indexed_files)

    # Build exclusion patterns
    patterns = list(DEFAULT_EXCLUDES)
    patterns.extend(_read_searchignore(root_path))

    # Scan current filesystem
    current_files = {}  # file_path (relative) -> content_hash
    for file_path in root_path.rglob("*"):
        if not file_path.is_file():
            continue

        if _should_exclude(file_path, root_path, patterns):
            continue

        try:
            rel_path = str(file_path.relative_to(root_path))
            content_hash = calculate_file_hash(file_path)
            current_files[rel_path] = content_hash
        except Exception:
            continue  # Skip unreadable files

    result["counts"]["total_current"] = len(current_files)

    # Compare sets
    indexed_set = set(indexed_files.keys())
    current_set = set(current_files.keys())

    # New files (on disk, not in index)
    new_files = sorted(current_set - indexed_set)
    result["new_files"] = new_files
    result["counts"]["new"] = len(new_files)

    # Deleted files (in index, not on disk)
    deleted_files = sorted(indexed_set - current_set)
    result["deleted_files"] = deleted_files
    result["counts"]["deleted"] = len(deleted_files)

    # Modified files (hash differs)
    common_files = indexed_set & current_set
    modified_files = []
    for file_path in sorted(common_files):
        if current_files[file_path] != indexed_files[file_path]:
            modified_files.append(file_path)

    result["modified_files"] = modified_files
    result["counts"]["modified"] = len(modified_files)
    result["counts"]["unchanged"] = len(common_files) - len(modified_files)

    return result


def cmd_grep_context(
    root_path: Path,
    pattern: str,
    context: int = 5,
    limit: int = 50,
    case_insensitive: bool = True,
) -> dict:
    """
    Pattern search with rich context - returns ALL matches with surrounding lines.

    Unlike regular search which returns one snippet per file, this returns
    every match with full context for batch analysis.

    Args:
        root_path: Root directory to search
        pattern: Search pattern (supports regex)
        context: Number of context lines before/after each match (default 5)
        limit: Maximum total matches to return (default 50)
        case_insensitive: Case-insensitive search (default True)

    Returns:
        Dictionary with:
        - pattern: Original pattern
        - context_lines: Context setting used
        - total_matches: Total matches found
        - files: Dictionary of filepath -> list of match objects
        - error: Error message if any

    Each match object contains:
        - line_number: 1-indexed line number of match
        - match_text: The matching line
        - before: List of lines before the match
        - after: List of lines after the match
    """
    pattern = validate_query(pattern)

    if not check_ripgrep():
        return {
            "pattern": pattern,
            "context_lines": context,
            "total_matches": 0,
            "files": {},
            "error": "ripgrep (rg) not found. Install with: sudo apt install ripgrep",
        }

    if not root_path.exists():
        return {
            "pattern": pattern,
            "context_lines": context,
            "total_matches": 0,
            "files": {},
            "error": f"Directory not found: {root_path}",
        }

    # Build ripgrep command with JSON output and context
    rg_args = ["rg", "--json"]
    if case_insensitive:
        rg_args.append("-i")
    rg_args.extend(["-C", str(context)])

    # Default exclusions
    default_excludes = [
        "*.pyc", "__pycache__/*", ".git/*", "node_modules/*",
        "*.jpg", "*.png", "*.gif", "*.pdf", "*.zip",
        ".vault_state/*", "*.db"
    ]
    for excl in default_excludes:
        rg_args.extend(["--glob", f"!{excl}"])

    rg_args.extend([pattern, str(root_path)])

    try:
        result = subprocess.run(
            rg_args,
            capture_output=True,
            text=True,
            timeout=60
        )
    except subprocess.TimeoutExpired:
        return {
            "pattern": pattern,
            "context_lines": context,
            "total_matches": 0,
            "files": {},
            "error": "Search timed out after 60 seconds",
        }

    # Parse JSON output and group by file
    # ripgrep JSON format: each line is a separate JSON object
    files: dict[str, list[dict]] = {}
    current_match: Optional[dict] = None
    current_file: Optional[str] = None
    total_matches = 0

    for line in result.stdout.strip().split('\n'):
        if not line:
            continue

        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get('type')
        data = entry.get('data', {})

        if entry_type == 'begin':
            # New file section
            path_obj = data.get('path', {})
            current_file = path_obj.get('text', '')
            if current_file and current_file not in files:
                files[current_file] = []

        elif entry_type == 'match':
            # A match line
            if total_matches >= limit:
                break

            path_obj = data.get('path', {})
            filepath = path_obj.get('text', '')
            line_num = data.get('line_number', 1)
            match_text = data.get('lines', {}).get('text', '').rstrip('\n')

            # Store the match
            current_match = {
                "line_number": line_num,
                "match_text": match_text,
                "before": [],
                "after": [],
            }

            if filepath not in files:
                files[filepath] = []
            files[filepath].append(current_match)
            total_matches += 1
            current_file = filepath

        elif entry_type == 'context':
            # Context line (before or after a match)
            if current_match is None or current_file is None:
                continue

            line_num = data.get('line_number', 0)
            context_text = data.get('lines', {}).get('text', '').rstrip('\n')
            match_line_num = current_match['line_number']

            # Determine if before or after
            if line_num < match_line_num:
                current_match['before'].append({
                    "line_number": line_num,
                    "text": context_text,
                })
            else:
                current_match['after'].append({
                    "line_number": line_num,
                    "text": context_text,
                })

        elif entry_type == 'end':
            # End of file section
            current_match = None

    return {
        "pattern": pattern,
        "context_lines": context,
        "total_matches": total_matches,
        "file_count": len(files),
        "files": files,
        "error": None,
    }


def cmd_relevant(
    root_path: Path,
    db_path: Path,
    query: str,
    top: int = 5,
) -> dict:
    """
    Pre-ranked file list - optimized for "which files should I read for this task?"

    Returns ONLY file paths with scores, no snippets. Smaller output = fewer tokens.

    Args:
        root_path: Root directory for ripgrep search
        db_path: Path to SQLite database for FTS search
        query: Search query
        top: Number of top files to return (default 5)

    Returns:
        Dictionary with:
        - query: Original query
        - top_files: List of {filepath, score} sorted by relevance
        - error: Error message if any
    """
    query = validate_query(query)

    # Run combined search to get ranked results
    try:
        combined_results = search_combined(
            root_path=root_path,
            db_path=db_path,
            query=query,
            limit=top,
            recency_boost=True,
        )
    except Exception as e:
        return {
            "query": query,
            "top_files": [],
            "error": str(e),
        }

    # Extract just filepath and score
    top_files = []
    for result in combined_results.get("results", []):
        top_files.append({
            "filepath": result["filepath"],
            "score": round(result["score"], 4),
        })

    return {
        "query": query,
        "top_files": top_files,
        "error": combined_results.get("stats", {}).get("fts_error") or
                 combined_results.get("stats", {}).get("ripgrep_error"),
    }
