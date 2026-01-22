#!/usr/bin/env python3
"""
Section Extraction - Generic section extraction for multiple file types.

Provides unified section extraction across:
- Markdown files: Header-based sections (#{1,6})
- Plain text: Blank line separators
- Log files: Timestamp-based entries
- Code files: Comment blocks, function/class definitions
- Default: Blank line separators

Sections include date detection from headers and nearby content.
"""

import re
from datetime import datetime
from typing import Optional

# =============================================================================
# Date Pattern Constants
# =============================================================================

# Date patterns with format identifiers for normalization
DATE_PATTERNS = [
    # ISO format: 2024-01-15 or 2024/01/15
    (re.compile(r'\b(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b'), 'ymd'),
    # US format: 01-15-2024 or 01/15/2024
    (re.compile(r'\b(0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])[-/](20\d{2})\b'), 'mdy'),
    # US format without leading zeros: 1-15-2024 or 1/15/2024
    (re.compile(r'\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b'), 'mdy_flexible'),
]

# File type mappings
MARKDOWN_EXTENSIONS = {'md', 'markdown', 'mdx'}
LOG_EXTENSIONS = {'log'}
CODE_EXTENSIONS = {
    'py', 'js', 'ts', 'jsx', 'tsx', 'java', 'c', 'cpp', 'h', 'hpp',
    'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'r',
    'sh', 'bash', 'zsh', 'fish', 'ps1', 'bat', 'cmd',
}
TEXT_EXTENSIONS = {'txt', 'text'}


# =============================================================================
# Date Utilities
# =============================================================================

def _validate_date(year: int, month: int, day: int) -> bool:
    """Validate that year, month, day form a valid date."""
    try:
        datetime(year, month, day)
        return True
    except ValueError:
        return False


def _normalize_to_iso(year: str, month: str, day: str) -> Optional[str]:
    """
    Normalize date components to ISO format YYYY-MM-DD.

    Returns None if the date is invalid.
    """
    try:
        y = int(year)
        m = int(month)
        d = int(day)

        # Validate ranges
        if not (2000 <= y <= 2099):
            return None
        if not (1 <= m <= 12):
            return None
        if not (1 <= d <= 31):
            return None

        # Validate actual date
        if not _validate_date(y, m, d):
            return None

        return f"{y:04d}-{m:02d}-{d:02d}"
    except (ValueError, TypeError):
        return None


def _extract_date_from_line(line: str) -> Optional[str]:
    """
    Extract and normalize a date from a line of text.

    Supports:
    - ISO: YYYY-MM-DD, YYYY/MM/DD
    - US: MM-DD-YYYY, MM/DD/YYYY

    Returns ISO format (YYYY-MM-DD) or None.
    """
    for pattern, fmt in DATE_PATTERNS:
        match = pattern.search(line)
        if match:
            groups = match.groups()

            if fmt == 'ymd':
                year, month, day = groups
            elif fmt == 'mdy':
                month, day, year = groups
            elif fmt == 'mdy_flexible':
                month, day, year = groups
                # Validate month/day ranges for ambiguous formats
                m, d = int(month), int(day)
                # If month > 12, it's likely day-first (European), but we treat as US
                if m > 12:
                    return None  # Invalid US format
            else:
                continue

            normalized = _normalize_to_iso(year, month, day)
            if normalized:
                return normalized

    return None


def detect_section_date(lines: list[str], start_line: int, max_scan: int = 5) -> Optional[str]:
    """
    Find a date within the first few lines of a section.

    Scans from the section header line downward to find embedded dates.
    This is useful for journal entries where the date is in or near the header.

    Args:
        lines: List of all file lines (0-indexed internally)
        start_line: 1-indexed line number to start scanning from
        max_scan: Maximum lines to scan (default 5)

    Returns:
        ISO format date string (YYYY-MM-DD) or None if no date found.

    Example:
        >>> lines = ["# My Journal", "#### 2024-01-15:", "Today I learned..."]
        >>> detect_section_date(lines, 1, max_scan=3)
        '2024-01-15'
    """
    if not lines or start_line < 1:
        return None

    # Convert to 0-indexed
    start_idx = start_line - 1
    if start_idx >= len(lines):
        return None

    end_idx = min(len(lines), start_idx + max_scan)

    for idx in range(start_idx, end_idx):
        date = _extract_date_from_line(lines[idx])
        if date:
            return date

    return None


# =============================================================================
# Section Pattern Matchers
# =============================================================================

def _is_markdown_header(line: str) -> Optional[int]:
    """
    Check if line is a markdown header, return header level (1-6) or None.
    """
    match = re.match(r'^(#{1,6})\s+', line)
    if match:
        return len(match.group(1))
    return None


def _is_blank_or_separator(line: str) -> bool:
    """Check if line is blank or a separator (---, ===, etc.)."""
    stripped = line.strip()
    if not stripped:
        return True
    if re.match(r'^[-=_*]{3,}\s*$', stripped):
        return True
    return False


def _is_log_timestamp(line: str) -> bool:
    """
    Check if line starts with a timestamp pattern common in log files.

    Matches patterns like:
    - 2024-01-15 10:30:45
    - [2024-01-15 10:30:45]
    - 2024/01/15 10:30
    - [INFO] 2024-01-15
    """
    patterns = [
        r'^\[?\d{4}[-/]\d{2}[-/]\d{2}[\s\]T]',  # ISO date at start
        r'^\[\w+\]\s*\d{4}[-/]\d{2}[-/]\d{2}',  # [LEVEL] date
        r'^\d{2}:\d{2}:\d{2}',  # Timestamp at start
    ]
    for pattern in patterns:
        if re.match(pattern, line):
            return True
    return False


def _is_code_block_start(line: str, file_ext: str) -> bool:
    """
    Check if line starts a significant code block (function, class, docstring).
    """
    stripped = line.strip()

    if file_ext in ('py',):
        # Python: function def, class def, docstring start
        if re.match(r'^(def|class|async\s+def)\s+\w+', stripped):
            return True
        if stripped.startswith('"""') or stripped.startswith("'''"):
            return True

    elif file_ext in ('js', 'ts', 'jsx', 'tsx'):
        # JavaScript/TypeScript: function, class, arrow functions, exports
        if re.match(r'^(function|class|export|const|let|var)\s+', stripped):
            return True
        if re.match(r'^(async\s+)?function', stripped):
            return True

    elif file_ext in ('java', 'c', 'cpp', 'cs', 'go', 'rs'):
        # C-family: function signatures, class definitions
        if re.match(r'^(public|private|protected|static|class|struct|func|fn)\s+', stripped):
            return True

    elif file_ext in ('rb',):
        # Ruby: def, class, module
        if re.match(r'^(def|class|module)\s+', stripped):
            return True

    elif file_ext in ('sh', 'bash', 'zsh'):
        # Shell: function definitions
        if re.match(r'^(\w+\s*\(\)|function\s+\w+)', stripped):
            return True

    return False


def _is_comment_block_start(line: str, file_ext: str) -> bool:
    """Check if line starts a comment block."""
    stripped = line.strip()

    # Multi-line comment starters
    if stripped.startswith('/*') or stripped.startswith('/**'):
        return True
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True

    # Shell/Python style comment blocks (multiple # lines often precede sections)
    if file_ext in ('py', 'sh', 'bash', 'zsh', 'rb', 'r'):
        if stripped.startswith('# =') or stripped.startswith('#==='):
            return True
        if stripped.startswith('# ---') or stripped.startswith('#---'):
            return True

    return False


# =============================================================================
# Section Extraction by File Type
# =============================================================================

def _extract_markdown_sections(lines: list[str]) -> list[dict]:
    """
    Extract sections from markdown content based on headers.

    Sections are delimited by header lines (^#{1,6} followed by space).
    Each section runs from its header to the line before the next header
    of equal or higher level (or end of file).
    """
    sections = []

    for i, line in enumerate(lines):
        level = _is_markdown_header(line)
        if level is not None:
            # Check for date in header
            date = _extract_date_from_line(line)

            sections.append({
                "line_start": i + 1,  # 1-indexed
                "line_end": None,  # Will be filled in
                "section_date": date,
                "section_header": line.strip(),
                "section_type": "md_header",
                "_level": level,  # Internal use for level tracking
            })

    # Fill in line_end values
    for i, section in enumerate(sections):
        if i + 1 < len(sections):
            # End is line before next section
            section["line_end"] = sections[i + 1]["line_start"] - 1
        else:
            # Last section goes to end of file
            section["line_end"] = len(lines)

        # Remove internal level tracking
        del section["_level"]

    # If no headers found, treat entire file as one section
    if not sections and lines:
        date = detect_section_date(lines, 1, max_scan=10)
        sections.append({
            "line_start": 1,
            "line_end": len(lines),
            "section_date": date,
            "section_header": lines[0].strip() if lines else "",
            "section_type": "md_header",
        })

    return sections


def _extract_text_sections(lines: list[str]) -> list[dict]:
    """
    Extract sections from plain text using blank line separators.

    Consecutive blank lines are treated as section boundaries.
    """
    sections = []
    current_start = None

    for i, line in enumerate(lines):
        is_blank = _is_blank_or_separator(line)

        if current_start is None and not is_blank:
            # Start of new section
            current_start = i
        elif current_start is not None and is_blank:
            # End of section (blank line found)
            header_line = lines[current_start].strip()
            date = detect_section_date(lines, current_start + 1, max_scan=5)

            sections.append({
                "line_start": current_start + 1,
                "line_end": i,  # End at the blank line
                "section_date": date,
                "section_header": header_line,
                "section_type": "blank_sep",
            })
            current_start = None

    # Handle last section (no trailing blank)
    if current_start is not None:
        header_line = lines[current_start].strip()
        date = detect_section_date(lines, current_start + 1, max_scan=5)

        sections.append({
            "line_start": current_start + 1,
            "line_end": len(lines),
            "section_date": date,
            "section_header": header_line,
            "section_type": "blank_sep",
        })

    return sections


def _extract_log_sections(lines: list[str]) -> list[dict]:
    """
    Extract sections from log files based on timestamp patterns.

    Each timestamped line starts a new section.
    """
    sections = []

    for i, line in enumerate(lines):
        if _is_log_timestamp(line):
            date = _extract_date_from_line(line)

            sections.append({
                "line_start": i + 1,
                "line_end": None,
                "section_date": date,
                "section_header": line.strip()[:100],  # Truncate long log lines
                "section_type": "log_timestamp",
            })

    # Fill in line_end values
    for i, section in enumerate(sections):
        if i + 1 < len(sections):
            section["line_end"] = sections[i + 1]["line_start"] - 1
        else:
            section["line_end"] = len(lines)

    # If no timestamps found, fall back to blank line separation
    if not sections:
        return _extract_text_sections(lines)

    return sections


def _extract_code_sections(lines: list[str], file_ext: str) -> list[dict]:
    """
    Extract sections from code files based on function/class definitions
    and comment blocks.
    """
    sections = []

    for i, line in enumerate(lines):
        if _is_code_block_start(line, file_ext) or _is_comment_block_start(line, file_ext):
            date = detect_section_date(lines, i + 1, max_scan=3)

            section_type = "code_def"
            if _is_comment_block_start(line, file_ext):
                section_type = "comment_block"

            sections.append({
                "line_start": i + 1,
                "line_end": None,
                "section_date": date,
                "section_header": line.strip()[:100],
                "section_type": section_type,
            })

    # Fill in line_end values
    for i, section in enumerate(sections):
        if i + 1 < len(sections):
            section["line_end"] = sections[i + 1]["line_start"] - 1
        else:
            section["line_end"] = len(lines)

    # If no code blocks found, fall back to blank line separation
    if not sections:
        return _extract_text_sections(lines)

    return sections


# =============================================================================
# Main Public Functions
# =============================================================================

def extract_sections(content: str, file_type: str) -> list[dict]:
    """
    Extract sections from file content.

    Detects structural markers appropriate for the file type and returns
    a list of sections with their boundaries and metadata.

    Args:
        content: The full text content of the file
        file_type: File extension without dot (e.g., 'md', 'py', 'txt')
                   or full filename if extension-less

    Returns:
        List of section dictionaries:
        {
            "line_start": int,      # 1-indexed first line of section
            "line_end": int | None, # 1-indexed last line, None = until next section
            "section_date": str | None,  # ISO format YYYY-MM-DD or None
            "section_header": str,  # The marker line (truncated if long)
            "section_type": str     # "date_header", "md_header", "blank_sep",
                                    # "log_timestamp", "code_def", "comment_block"
        }

    Section detection strategies by file type:
        - Markdown (md, markdown, mdx): #{1,6} headers
        - Plain text (txt, text): Blank line separators
        - Logs (log): Timestamp patterns at line start
        - Code (py, js, etc.): Function/class definitions, comment blocks
        - Default: Blank line separators

    Example:
        >>> content = '''# Introduction
        ... This is the intro.
        ...
        ... ## Chapter 1
        ... #### 2024-01-15:
        ... Today's entry.
        ... '''
        >>> sections = extract_sections(content, 'md')
        >>> len(sections)
        3
        >>> sections[2]['section_date']
        '2024-01-15'
    """
    if not content:
        return []

    # Normalize line endings and split
    lines = content.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    # Remove trailing empty lines for consistent counting
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return []

    # Normalize file type
    file_ext = file_type.lower().lstrip('.')

    # Select extraction strategy based on file type
    if file_ext in MARKDOWN_EXTENSIONS:
        sections = _extract_markdown_sections(lines)
    elif file_ext in LOG_EXTENSIONS:
        sections = _extract_log_sections(lines)
    elif file_ext in CODE_EXTENSIONS:
        sections = _extract_code_sections(lines, file_ext)
    elif file_ext in TEXT_EXTENSIONS:
        sections = _extract_text_sections(lines)
    else:
        # Default fallback: blank line separation
        sections = _extract_text_sections(lines)

    # Post-process: if section header contains a date, mark as date_header
    for section in sections:
        if section["section_date"] and section["section_type"] in ("md_header", "blank_sep"):
            # Check if the date is actually in the header line
            if section["section_date"] in section["section_header"].replace('/', '-'):
                section["section_type"] = "date_header"

    return sections


def get_section_for_line(sections: list[dict], line_number: int) -> Optional[dict]:
    """
    Find which section contains a given line number.

    Args:
        sections: List of sections from extract_sections()
        line_number: 1-indexed line number to locate

    Returns:
        The section dict containing the line, or None if not found.
    """
    for section in sections:
        if section["line_start"] <= line_number:
            if section["line_end"] is None or line_number <= section["line_end"]:
                return section
    return None


def get_section_content(content: str, section: dict) -> str:
    """
    Extract the text content of a specific section.

    Args:
        content: The full file content
        section: A section dict from extract_sections()

    Returns:
        The text content of that section.
    """
    lines = content.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    start_idx = section["line_start"] - 1
    end_idx = section["line_end"] if section["line_end"] else len(lines)

    return '\n'.join(lines[start_idx:end_idx])


# =============================================================================
# Module Self-Test
# =============================================================================

if __name__ == '__main__':
    import sys

    # Test content
    test_md = """# My Journal

## January 2024

#### 2024-01-15:
Today I learned about section extraction.

#### 2024-01-16:
More work on the project.

## February 2024

#### 2024-02-01:
New month, new goals.
"""

    test_py = '''#!/usr/bin/env python
"""
Module docstring here.
Created: 2024-01-15
"""

# =============================================================================
# Constants
# =============================================================================

DEFAULT_VALUE = 42


def my_function(x):
    """Do something with x."""
    return x * 2


class MyClass:
    """A sample class."""

    def method(self):
        pass
'''

    test_log = """2024-01-15 10:30:45 [INFO] Application started
2024-01-15 10:30:46 [DEBUG] Loading configuration
2024-01-15 10:31:00 [ERROR] Connection failed
2024-01-16 08:00:00 [INFO] Retry successful
"""

    test_txt = """First paragraph of text.
This is still the first section.

Second paragraph starts here.
More content in section two.

Third section.
"""

    print("=" * 60)
    print("Section Extraction Tests")
    print("=" * 60)

    # Test markdown
    print("\n--- Markdown Sections ---")
    sections = extract_sections(test_md, 'md')
    for s in sections:
        print(f"  Lines {s['line_start']}-{s['line_end']}: "
              f"[{s['section_type']}] {s['section_header'][:40]}... "
              f"date={s['section_date']}")

    # Test Python
    print("\n--- Python Code Sections ---")
    sections = extract_sections(test_py, 'py')
    for s in sections:
        print(f"  Lines {s['line_start']}-{s['line_end']}: "
              f"[{s['section_type']}] {s['section_header'][:40]}...")

    # Test log
    print("\n--- Log Sections ---")
    sections = extract_sections(test_log, 'log')
    for s in sections:
        print(f"  Lines {s['line_start']}-{s['line_end']}: "
              f"[{s['section_type']}] date={s['section_date']}")

    # Test plain text
    print("\n--- Plain Text Sections ---")
    sections = extract_sections(test_txt, 'txt')
    for s in sections:
        print(f"  Lines {s['line_start']}-{s['line_end']}: "
              f"[{s['section_type']}] {s['section_header'][:30]}...")

    # Test detect_section_date
    print("\n--- Date Detection ---")
    test_lines = [
        "# Header without date",
        "#### 2024-01-15:",
        "Some content here",
        "Entry dated 01/20/2024",
    ]

    for i, line in enumerate(test_lines, 1):
        date = detect_section_date(test_lines, i, max_scan=1)
        print(f"  Line {i}: date={date} | {line[:40]}")

    print("\n" + "=" * 60)
    print("All tests completed.")
