# AI-grep - Token-Efficient Directory Search for AI Assistants

A portable, drop-in search tool that indexes any directory. Designed specifically for **AI/LLM workflows** where token efficiency matters - find what you need without reading entire files.

**Use cases:**
- Codebases (source code, configs, docs)
- Obsidian vaults (markdown notes with wiki-links)
- Documentation folders (mixed file types)
- Any directory you want searchable

---

## Why This Tool?

When AI assistants explore codebases, they burn tokens reading files to find relevant content. This tool provides **targeted retrieval** - get exactly what you need in minimal tokens.

| Problem | Solution |
|---------|----------|
| AI reads entire files to find relevant code | `relevant` returns ranked file list (~100 tokens) |
| Exploring codebase structure wastes tokens | `stats` + `toc` give overview (~500 tokens) |
| Need context around a specific line | `context --line N` returns just that section |
| grep is slow on large directories | SQLite FTS5 index with incremental updates |
| Can't search inside docx/xlsx/pdf | Extracts text from Office documents |

---

## Quick Start

```bash
# 1. Copy to your project
cp -r search-tool/ /your/project/
cd /your/project/search-tool
chmod +x ai-grep

# 2. Setup and index (one-time)
./ai-grep setup

# 3. Search
./ai-grep search "authentication"
./ai-grep relevant "user login" --top 5
./ai-grep get "src/auth.py" --lines 10-50
```

---

## AI/LLM Workflow

Optimized for minimal token usage:

```bash
# 1. ORIENT - Understand the codebase (do this once)
./ai-grep stats                      # Overview in ~500 tokens
./ai-grep toc --type python          # All Python files (~50 tokens/file)

# 2. LOCATE - Find relevant files
./ai-grep relevant "your task" --top 5   # Just paths + scores (~100 tokens)
./ai-grep refs "ClassName"               # Find all references

# 3. RETRIEVE - Get specific content
./ai-grep get "path.py" --lines 10-50    # Only the lines you need
./ai-grep context "path.py" --line 42    # Context around a line

# 4. ANALYZE - Understand relationships
./ai-grep related "path.py" --top 3      # Similar files
./ai-grep outline "path.py"              # Structure without content
```

### Token Estimates

Typical source file: 200-500 lines ≈ 4,000-10,000 tokens

| Operation | Without Tool | With Tool | Savings |
|-----------|-------------|-----------|---------|
| Understand 50-file codebase | ~250,000 tokens (read all) | ~500 tokens (`stats`) | **99.8%** |
| Find relevant files | ~50,000 tokens (read 10 candidates) | ~100 tokens (`relevant --top 5`) | **99.8%** |
| Read function implementation | ~5,000 tokens (full file) | ~600 tokens (`get --lines 10-50`) | **88%** |
| Understand file structure | ~5,000 tokens (full file) | ~150 tokens (`outline`) | **97%** |
| List all Python files | ~10,000 tokens (glob + read headers) | ~500 tokens (`toc --type python`) | **95%** |

**Real-world example:** Exploring a 100-file codebase to fix a bug
- Traditional: Read 10+ files to find relevant code ≈ 50,000-100,000 tokens
- With tool: `stats` → `relevant` → `get --lines` ≈ 1,500-3,000 tokens
- **Savings: 95-97%**

---

## Integrating with AI Assistants

Add to your AI assistant's system prompt (e.g. CLAUDE.md)or instructions file:

### Minimal Version

```markdown
## Local Search

Use `./ai-grep` in this codebase for token-efficient exploration:
- `./ai-grep stats` - codebase overview
- `./ai-grep relevant "query" --top 5` - find relevant files
- `./ai-grep get "file.py" --lines N-M` - read specific lines
- `./ai-grep outline "file.py"` - file structure
```

### Detailed Version (Recommended)

```markdown
## Local Search Tool

This codebase has a local search tool at `./ai-grep`. Use it instead of
reading files directly to save tokens and speed up exploration.

### Workflow

1. **ORIENT** (do once per session):
   - `./ai-grep stats` - understand codebase size and structure
   - `./ai-grep toc --type python` - see all Python files

2. **LOCATE** (find what you need):
   - `./ai-grep relevant "your query" --top 5` - ranked file list
   - `./ai-grep search "keyword"` - full-text search with snippets
   - `./ai-grep refs "ClassName"` - find all references

3. **RETRIEVE** (get specific content):
   - `./ai-grep get "file.py" --lines 10-50` - specific line range
   - `./ai-grep context "file.py" --line 42 --around 10` - context around line
   - `./ai-grep outline "file.py"` - structure without full content

4. **ANALYZE** (understand relationships):
   - `./ai-grep related "file.py" --top 3` - similar files
   - `./ai-grep links` - internal link validation

### Rules
- NEVER read entire files when you only need a section
- ALWAYS use `relevant` before reading multiple files
- Use `outline` to understand structure before diving into code
- Use `--lines N-M` to fetch only the lines you need
```

---

## Command Reference

### Setup & Maintenance

```bash
./ai-grep setup                      # Initialize database and index
./ai-grep setup --no-index           # Initialize only, skip indexing
./ai-grep index                      # Re-index files (incremental)
./ai-grep index --force              # Force full re-index
./ai-grep status                     # Show index statistics
./ai-grep validate                   # Check database integrity
./ai-grep config                     # Show current configuration
```

### Search & Retrieval

```bash
# Search
./ai-grep search "query"             # Find files matching query
./ai-grep search "query" --limit 100 # Return up to 100 results

# Retrieve content
./ai-grep get "file.py"              # Full content (JSON)
./ai-grep get "file.py" --raw        # Full content (raw text)
./ai-grep get "file.py" --lines 10-50 # Specific line range
./ai-grep context "file.py" --line 42 --around 10  # Context around line
./ai-grep bundle "a.py,b.py,c.py" --raw  # Multiple files at once

# List files
./ai-grep list                       # All indexed files
./ai-grep list --type python         # Filter by type
./ai-grep list --pattern "test"      # Filter by path pattern
./ai-grep list --recent 20           # Most recent files
```

### Analysis & Discovery

```bash
# Codebase overview
./ai-grep stats                      # File counts, sizes, types
./ai-grep timeline --days 7          # Files modified recently
./ai-grep toc                        # Table of contents for all files
./ai-grep toc --type markdown        # TOC filtered by type

# File structure
./ai-grep outline "src/main.py"      # Headers, functions, classes
./ai-grep tags                       # Frontmatter, hashtags, TODOs
```

### Content Analysis

```bash
# Find similar content
./ai-grep related "src/auth.py" --top 5   # Similar files (TF-IDF)
./ai-grep duplicates                       # Exact and near-duplicates
./ai-grep refs "UserModel" --context 3     # Find symbol references

# Link validation (for Obsidian/wikis)
./ai-grep links                            # Extract and validate links
```

### Multi-Directory Indexing

```bash
# Mount multiple directories into one searchable index
./ai-grep mount ~/notes notes        # Add notes directory
./ai-grep mount ~/code code          # Add code directory
./ai-grep sources                    # List all mounted directories
./ai-grep unmount notes              # Remove from index

# Search spans all sources
./ai-grep search "project ideas"     # Searches notes and code
```

### Export & Integration

```bash
# Export search results
./ai-grep export "query" --format json     # JSON output
./ai-grep export "query" --format csv      # CSV format
./ai-grep export "query" --format md       # Markdown format

# Clipboard & editor
./ai-grep clip "file.py"             # Copy to clipboard
./ai-grep open "file.py" --line 42   # Open in $EDITOR

# Query history
./ai-grep history                    # Show recent searches
./ai-grep history --clear            # Clear history
```

### Search Enhancements

```bash
# Track changes
./ai-grep diff                       # What changed since last index

# Pattern search with context
./ai-grep grep-context "def.*auth" --context 5

# Token-efficient file ranking
./ai-grep relevant "authentication" --top 5     # Just paths + scores
```

---

## Installation

### Prerequisites

| Dependency | Purpose | Installation |
|------------|---------|--------------|
| Python 3.9+ | Runtime | Usually pre-installed |
| ripgrep | Fast file search | `sudo apt install ripgrep` or `brew install ripgrep` |

Optional (auto-installed by setup if missing):
- `python-docx` - Word document extraction
- `openpyxl` - Excel spreadsheet extraction
- `PyPDF2` - PDF text extraction
- `pyperclip` - Clipboard support

### Deployment Strategies

**Choose based on your needs:**

#### Option A: Central Install (Recommended for AI workflows)

Keep ONE copy of the tool and mount multiple directories into it. Perfect for a global AI instructions file that works across all your projects.

```bash
# Install once in a central location
mkdir -p ~/tools/search
cp -r search-tool/* ~/tools/search/
cd ~/tools/search
chmod +x ai-grep
./ai-grep setup

# Mount your project directories
./ai-grep mount ~/projects/webapp webapp
./ai-grep mount ~/projects/api api
./ai-grep mount ~/notes notes

# Search across everything
./ai-grep search "authentication"  # Searches all mounted dirs
```

Then in your **global AI instructions** (e.g., `~/.claude/CLAUDE.md`):
```markdown
## Global Search Tool

Use `~/tools/search/ai-grep` for searching any of my projects:
- `~/tools/search/ai-grep relevant "query" --top 5`
- `~/tools/search/ai-grep get "webapp/src/auth.py" --lines 10-50`
```

#### Option B: Per-Project Copy

Copy the tool into each project. Better for isolated projects or sharing with others.

```bash
cp -r search-tool/ /your/project/
cd /your/project/search-tool
chmod +x ai-grep
./ai-grep setup
```

Then in your **project's instructions** (e.g., `CLAUDE.md` in project root):
```markdown
## Local Search Tool

Use `./search-tool/ai-grep` for this codebase.
```

#### Option C: Global Symlink

Symlink for convenience, but each directory gets its own index.

```bash
ln -s /path/to/search-tool/ai-grep ~/.local/bin/search
cd /any/project
search setup  # Creates SEARCH/ index in current directory
```

---

## Configuration

### .searchignore

Gitignore-style exclusion patterns (created automatically):

```gitignore
# Version control
.git/

# Dependencies
node_modules/
.venv/
__pycache__/

# Build outputs
dist/
build/

# Binary files
*.zip
*.jpg
*.png

# Logs
*.log
tmp/
```

---

## Directory Structure

```
your-project/
├── search-tool/               # Search tool (copy this folder)
│   ├── ai-grep              # CLI executable
│   ├── vault_lib/             # Python modules
│   │   ├── setup.py           # Dependency and DB initialization
│   │   ├── index.py           # Incremental indexing
│   │   ├── ai-grep          # FTS5 + ripgrep search
│   │   ├── file_extract.py    # Content extraction
│   │   ├── analysis.py        # stats, timeline, tags, outline
│   │   ├── similarity.py      # related, duplicates, links
│   │   ├── sources.py         # mount, unmount
│   │   └── export.py          # export, clip, open, history
│   └── SEARCH/                # Created by setup
│       ├── .vault.db          # SQLite FTS5 database
│       ├── config.json        # Configuration
│       └── .searchignore      # Exclusion patterns
├── src/                       # Your project files (indexed)
└── docs/                      # Your docs (indexed)
```

---

## Troubleshooting

```bash
# "Database not initialized"
./ai-grep setup

# "ripgrep (rg) not found"
sudo apt install ripgrep  # Linux
brew install ripgrep      # macOS

# Search returns no results
./ai-grep status              # Check file count
./ai-grep index --force       # Force re-index

# Database corrupted
rm ./SEARCH/.vault.db
./ai-grep setup
```

---

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Should work (ripgrep via Homebrew) |
| WSL | Should work |
| Windows | Not tested |

---

## License

MIT License - Use freely, modify as needed.
