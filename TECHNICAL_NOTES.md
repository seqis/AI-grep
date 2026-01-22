# TECHNICAL_NOTES.md - AI-grep Architecture

This document captures architectural decisions and technical details for future sessions working on this codebase.

---

## 1. Architecture Overview

### High-Level Design

The SEARCH tool follows a **setup -> index -> search** pipeline:

```
[User's Directory]
        |
        v
    ./ai-grep setup      --> Creates SEARCH/ with .vault.db, config.json, .searchignore
        |
        v
    ./ai-grep index      --> Scans files, extracts content, stores in SQLite FTS5
        |
        v
    ./ai-grep search     --> Ripgrep + FTS5 combined search with deduplication
```

### Design Goals

- **Portable**: Copy to any directory and use immediately
- **Self-contained**: All dependencies in `vault_lib/`, no external module requirements
- **AI-optimized**: Commands return minimal tokens for efficient LLM workflows
- **Incremental**: Only re-indexes changed files for fast updates

### Module Responsibilities

| Module              | Purpose                                                            |
| ------------------- | ------------------------------------------------------------------ |
| `ai-grep`         | CLI entry point, auto-sync orchestration, command dispatch         |
| `vault_lib/setup.py`| Dependency checking, DB creation, schema initialization            |
| `vault_lib/index.py`| Incremental indexing, change detection, manifest tracking          |
| `vault_lib/ai-grep`| FTS5 search, ripgrep search, combined search with deduplication   |
| `vault_lib/file_extract.py` | Content extraction from text, docx, xlsx, pdf              |

---

## 2. Schema Design (CRITICAL)

### SQLite Schema Created by `setup.py`

**WARNING**: There is a documented/implementation mismatch in `ai-grep`. The docstring at the top of `ai-grep` shows an INCORRECT schema (uses `filepath`, `id`, `fts_files`). The ACTUAL schema is below.

#### `files` table (metadata + content)
```sql
CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL,       -- Relative path from root
    filename TEXT NOT NULL,               -- Just the filename
    file_type TEXT,                       -- 'markdown', 'python', 'text', etc.
    content TEXT NOT NULL,                -- Full extracted content
    content_hash TEXT NOT NULL,           -- SHA-256 first 16 chars
    file_size INTEGER,                    -- Bytes
    modified_at TEXT,                     -- File mtime (ISO format)
    indexed_at TEXT NOT NULL              -- When indexed (ISO format)
)
```

#### `files_fts` table (FTS5 virtual table)
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    file_path,
    filename,
    content,
    content='files',
    content_rowid='file_id',
    tokenize='porter unicode61'
)
```

#### Triggers (FTS sync)
- `files_ai` - After INSERT on files, insert into FTS
- `files_ad` - After DELETE on files, delete from FTS
- `files_au` - After UPDATE on files, update FTS

#### `index_runs` table (tracking)
```sql
CREATE TABLE IF NOT EXISTS index_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    file_count INTEGER,
    total_size INTEGER,
    status TEXT DEFAULT 'running'
)
```

#### `manifest` table (created by `index.py`, NOT `setup.py`)
```sql
CREATE TABLE IF NOT EXISTS manifest (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_indexed_at TEXT NOT NULL,
    total_files INTEGER NOT NULL,
    content_hash TEXT NOT NULL
)
```

**Key insight**: `setup.py` creates the files/FTS schema. `index.py` creates the manifest table on first index. This split exists because manifest only makes sense after indexing.

### Why This Schema

1. **FTS5 with content sync**: Using `content='files'` makes the FTS table reference the main table, keeping them in sync via triggers
2. **Porter stemmer**: `tokenize='porter unicode61'` enables stemming (e.g., "running" matches "run")
3. **Content hash**: 16-character SHA-256 prefix for efficient change detection without full content comparison
4. **Manifest singleton**: `CHECK (id = 1)` ensures only one manifest row exists

---

## 3. Indexing Strategy

### Incremental Update Algorithm

```python
# Pseudocode for index_files()
indexed_files = get_indexed_files(db)  # {file_path: content_hash}
current_files = scan_directory()        # {file_path: content_hash}

to_add = current_files - indexed_files      # New files
to_delete = indexed_files - current_files   # Removed files
to_check = indexed_files & current_files    # Existing files

# Check for content changes
to_update = {f for f in to_check if current_files[f] != indexed_files[f]}
```

### Change Detection

Files are re-indexed when:
1. File is new (not in DB)
2. File hash differs (content changed)
3. File removed (cleanup)

Hash calculation: `hashlib.sha256(file_bytes).hexdigest()[:16]`

### Content Extraction Pipeline

```
file_extract.py flow:
  1. detect_file_type(path) -> 'text', 'docx', 'xlsx', 'pdf', 'binary', 'unknown'
  2. Route to appropriate extractor:
     - text: Try UTF-8 -> UTF-8-sig -> Latin-1 -> CP1252 -> ASCII
     - docx: python-docx (paragraphs + tables)
     - xlsx: openpyxl (all sheets, cell values)
     - pdf: PyPDF2 (page-by-page text extraction)
  3. Return {filepath, content, file_type, size, extracted_at}
```

### .searchignore Support

Located at `SEARCH/.searchignore`. Uses gitignore-style patterns:
- `*.pyc` - Skip compiled Python
- `node_modules/` - Skip entire directory
- `.git/` - Skip version control

Default exclusions in code: `./SEARCH`, `.git`, `__pycache__`, `*.pyc`, `.DS_Store`, `*.db`

---

## 4. Search Strategy

### Combined Search Architecture

```
search_combined()
    |
    +---> search_fts()     --> SQLite FTS5 with BM25 ranking
    |                          Returns ranked results by relevance + recency
    |
    +---> search_ripgrep() --> ripgrep subprocess with JSON output
    |                          Returns results with match count + context
    |
    +---> Deduplicate by filepath
    |
    +---> Merge scores:
          FTS_WEIGHT = 0.6
          RIPGREP_WEIGHT = 0.4
          BOTH_SOURCES_BONUS = 0.2  (if found by both)
    |
    +---> Sort by final_score descending
```

### When Each Method Is Used

| Method   | Strengths                                    | Weaknesses                           |
| -------- | -------------------------------------------- | ------------------------------------ |
| FTS5     | Ranked by relevance, handles stemming, fast  | Requires index, phrase search limits |
| Ripgrep  | Exact matches, regex support, always works   | No ranking, can be noisy             |
| Combined | Best of both, handles partial DB failures    | Slightly more latency                |

### Performance Characteristics

| Operation      | Typical Time    | Notes                                    |
| -------------- | --------------- | ---------------------------------------- |
| Ripgrep search | 100-500ms       | Always works, no index needed            |
| FTS5 search    | 50-200ms        | Requires indexed DB                      |
| Combined       | 200-700ms       | Runs both in sequence, deduplicates      |
| Initial index  | 1-5s            | Depends on file count and types          |
| Incremental    | 100-500ms       | Only changed files                       |

---

## 5. Design Trade-offs

### SQLite + Ripgrep Combination

**Chose**: Dual search over single method

**Rationale**:
- FTS5 gives ranked results and stemming
- Ripgrep gives exact matches and regex
- Combined approach handles partial failures gracefully
- If FTS fails, ripgrep still works (and vice versa)

### Auto-Sync on Search

**Chose**: Check staleness (5 min threshold) and reindex before search

**Rationale**:
- User doesn't need to remember to index
- Incremental updates are fast (~100-500ms)
- Trade-off: First search after changes has latency
- Alternative considered: inotify watching (rejected: complexity, cross-platform issues)

### Content Stored in DB

**Chose**: Store full content in `files.content` column

**Trade-off**:
- Pro: Search snippets available immediately
- Pro: No filesystem access during search
- Con: Larger DB size (roughly 1.2x original file sizes)
- Con: Duplicate storage (files + DB copy)

### Python-Based Extraction

**Chose**: python-docx, openpyxl, PyPDF2 over system utilities

**Rationale**:
- Portability: Same behavior across systems
- Control: Consistent output formatting
- Trade-off: Requires pip packages (auto-installed by setup)
- Alternative considered: `pandoc`, `pdftotext` (rejected: extra system deps)

---

## 6. Known Limitations

### Platform

- **Linux-only**: Tested on Linux, may work on macOS
- **Requires ripgrep**: `sudo apt install ripgrep`
- **Python 3.8+**: Uses walrus operator, type hints

### FTS5 False Errors

**Symptom**: "no such table: fts_files" when tables actually exist

**Cause**: The docstring in `ai-grep` documents wrong table name (`fts_files` vs actual `files_fts`)

**Current behavior**: Combined search catches this and falls back to ripgrep-only

**Workaround**: If FTS search fails but DB has tables, ripgrep still provides results

### Large Files

- Files >1MB may slow indexing significantly
- PDF extraction can be slow for large documents
- Consider adding to `.searchignore` if too slow

### No Real-Time Watching

- Uses poll-based staleness check (5 min threshold)
- Files changed between index and search may be stale
- Run `./ai-grep index` for immediate refresh

### Binary Detection

- Some binary files may be misidentified as text
- Will fail gracefully (skip with warning)
- Add to `.searchignore` if causing issues

---

## 7. Debugging Notes for Future Sessions

### Schema Mismatch Symptoms

**Error**: `no such column: filepath`
**Cause**: Code using wrong column name
**Fix**: Check column names:
- Correct: `file_path`, `file_id`, `files`, `files_fts`
- Wrong (from old docstring): `filepath`, `id`, `fts_files`

### Stale Database Issues

**Symptoms**: Search returns no results, or old results

**Steps**:
1. Check DB exists: `ls -la SEARCH/.vault.db`
2. Validate: `./ai-grep validate`
3. If corrupted: `rm SEARCH/.vault.db && ./ai-grep index`

### Import Errors

**`__init__.py` should be minimal**: Only contains version and docstring
**If import fails**: Check sys.path in `ai-grep` includes parent directory

### Setup Creates DB But Index Fails

**Check**: Does `manifest` table exist?

`setup.py` creates: `files`, `files_fts`, `index_runs`, `schema_version`
`index.py` creates: `manifest`

If manifest missing after setup, that's expected. It's created on first `./ai-grep index`.

### FTS Triggers Not Firing

**Symptom**: Data in `files` but not in `files_fts`

**Check**: Triggers exist
```sql
SELECT name FROM sqlite_master WHERE type='trigger';
-- Should show: files_ai, files_ad, files_au
```

**Fix**: If triggers missing, delete DB and re-run setup

---

## 8. Extension Points

### Adding New File Type Support

1. Edit `vault_lib/file_extract.py`
2. Add extension to appropriate set (e.g., `TEXT_EXTENSIONS`)
3. If special handling needed, add `_extract_<type>()` function
4. Update `detect_file_type()` switch

**Example**: Adding `.rtf` support
```python
RTF_EXTENSIONS = {'.rtf'}

def _extract_rtf(filepath: Path) -> Optional[str]:
    # Use striprtf or similar library
    pass
```

### Changing Search Strategy

Edit `vault_lib/ai-grep`:
- Adjust weights: `FTS_WEIGHT`, `RIPGREP_WEIGHT`, `BOTH_SOURCES_BONUS`
- Change recency decay: `RECENCY_DECAY_FACTOR`
- Modify BM25 column weights in `search_fts()`

### Adding Filtering

Current filters: file_type (FTS only)

To add date filtering:
1. Add `--after` / `--before` args to CLI
2. Modify SQL queries to include `WHERE indexed_at > ?`

### Performance Tuning

**SQLite pragmas** (add to `_get_connection()`):
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
```

**Ripgrep flags** (in `search_ripgrep()`):
- `--mmap` for large files
- `-j4` for parallel threads
- `--max-filesize 10M` to skip huge files

---

## 9. Testing Approach

### Test in Isolated Directory

```bash
# Create test environment
mkdir -p /tmp/search_test
cd /tmp/search_test

# Copy tool
cp -r /path/to/SEARCH/ai-grep .
cp -r /path/to/SEARCH/vault_lib .

# Create test files
echo "hello world" > test.txt
echo "# Markdown Test\n\nSome content" > test.md
echo "print('python test')" > test.py
# For docx/xlsx/pdf, copy real files or use generators
```

### Verification Steps

1. **Setup creates files**:
   ```bash
   ./ai-grep setup
   ls -la SEARCH/  # Should show .vault.db, config.json, .searchignore
   ```

2. **Index processes files**:
   ```bash
   ./ai-grep index
   # Should report: added: N, updated: 0, deleted: 0
   ```

3. **Search returns results**:
   ```bash
   ./ai-grep search "hello"
   # Should return test.txt with snippet
   ```

### Key Test Files Needed

| Type  | Purpose                          | Example                          |
| ----- | -------------------------------- | -------------------------------- |
| .py   | Text extraction, Python type     | `print("test")`                  |
| .txt  | Basic text                       | `hello world`                    |
| .md   | Markdown detection               | `# Header\n\nParagraph`          |
| .docx | Word extraction                  | Any Word document                |
| .xlsx | Excel extraction                 | Any Excel spreadsheet            |
| .pdf  | PDF extraction                   | Any PDF with text (not scanned)  |

---

*Last updated: 2026-01-22*
*Version: 1.0.0*
