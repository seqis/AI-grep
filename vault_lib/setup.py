#!/usr/bin/env python3
"""
Setup module for portable folder search tool.

Handles:
- System dependency checking (ripgrep, sqlite3)
- Python package dependency checking (python-docx, openpyxl, PyPDF2)
- Interactive installation with sudo prompt
- SEARCH directory structure creation
- Configuration and manifest file initialization

Usage:
    from vault_lib.setup import check_dependencies, create_search_dir, validate_setup

    # Check what's missing
    deps = check_dependencies()
    if not deps['all_satisfied']:
        print("Missing dependencies:", deps)

    # Create search directory structure
    create_search_dir('/path/to/folder')

    # Validate setup
    status = validate_setup('/path/to/folder')
    if status['valid']:
        print("Ready to index!")
"""

import getpass
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# Default configuration for new search directories
DEFAULT_CONFIG = {
    "patterns": [
        "*.md",
        "*.txt",
        "*.pdf",
        "*.docx",
        "*.doc",
        "*.xlsx",
        "*.xls",
        "*.csv",
        "*.json",
        "*.yaml",
        "*.yml",
        "*.html",
        "*.htm",
        "*.xml",
        "*.rst",
        "*.org",
    ],
    "exclude_rules": [
        ".git",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        "*.pyc",
        "*.pyo",
        ".DS_Store",
        "Thumbs.db",
        "*.tmp",
        "*.temp",
        "*.bak",
        "*.swp",
        "*.swo",
        "*~",
    ],
    "indexed_at": None,
    "version": "1.0.0",
}

# Default .searchignore content (gitignore-style)
DEFAULT_SEARCHIGNORE = """# Searchignore - exclude patterns for folder indexing
# Uses gitignore syntax

# Version control
.git/
.svn/
.hg/

# Dependencies
node_modules/
vendor/
bower_components/

# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
env/
.eggs/
*.egg-info/

# Build outputs
dist/
build/
*.egg
*.whl

# IDE/Editor
.idea/
.vscode/
*.swp
*.swo
*~
.project
.classpath

# OS files
.DS_Store
Thumbs.db
Desktop.ini

# Logs and databases (not ours)
*.log
*.sqlite
*.sqlite3

# Temporary files
*.tmp
*.temp
*.bak
tmp/
temp/

# Binary files (large)
*.zip
*.tar.gz
*.rar
*.7z
*.exe
*.dll
*.so
*.dylib

# Media (usually not text-searchable)
*.mp3
*.mp4
*.wav
*.avi
*.mov
*.mkv
*.jpg
*.jpeg
*.png
*.gif
*.bmp
*.ico
*.psd
*.ai

# Compiled
*.o
*.obj
*.class
"""

# Required system packages (apt-get)
REQUIRED_APT_PACKAGES = {
    "ripgrep": "rg",  # package name -> binary name
}

# Required Python packages
REQUIRED_PIP_PACKAGES = [
    "python-docx",  # For .docx files
    "openpyxl",     # For .xlsx files
    "PyPDF2",       # For .pdf files
]


def check_dependencies() -> dict:
    """
    Check all system and Python dependencies.

    Returns:
        Dictionary with:
        - all_satisfied: bool - True if everything is installed
        - missing_apt: list - apt packages that need installation
        - missing_pip: list - pip packages that need installation
        - checked_apt: list - all apt packages checked
        - checked_pip: list - all pip packages checked
        - details: dict - detailed status of each dependency
    """
    result = {
        "all_satisfied": True,
        "missing_apt": [],
        "missing_pip": [],
        "checked_apt": list(REQUIRED_APT_PACKAGES.keys()),
        "checked_pip": REQUIRED_PIP_PACKAGES.copy(),
        "details": {},
    }

    # Check system packages (apt)
    for package, binary in REQUIRED_APT_PACKAGES.items():
        path = shutil.which(binary)
        if path:
            result["details"][package] = {"installed": True, "path": path}
        else:
            result["details"][package] = {"installed": False, "path": None}
            result["missing_apt"].append(package)
            result["all_satisfied"] = False

    # Check sqlite3 CLI (usually comes with sqlite3 package)
    sqlite_path = shutil.which("sqlite3")
    result["details"]["sqlite3"] = {
        "installed": sqlite_path is not None,
        "path": sqlite_path,
        "note": "Optional CLI tool, Python sqlite3 module is sufficient",
    }

    # Check Python packages
    for package in REQUIRED_PIP_PACKAGES:
        # Convert package name to module name for import check
        module_name = package.replace("-", "_").lower()
        if module_name == "python_docx":
            module_name = "docx"
        elif module_name == "pypdf2":
            module_name = "PyPDF2"

        spec = importlib.util.find_spec(module_name)
        if spec:
            result["details"][package] = {"installed": True, "module": module_name}
        else:
            result["details"][package] = {"installed": False, "module": module_name}
            result["missing_pip"].append(package)
            result["all_satisfied"] = False

    return result


def prompt_for_sudo(command: str) -> bool:
    """
    Prompt user for sudo password and run command interactively.

    This function does NOT store the password. It asks the user to enter
    their password directly via sudo's own prompt.

    Args:
        command: The command to run with sudo (without 'sudo' prefix)

    Returns:
        True if command succeeded, False otherwise
    """
    print(f"\nThe following command requires sudo privileges:")
    print(f"  sudo {command}")
    print()

    confirm = input("Proceed with installation? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Installation cancelled.")
        return False

    try:
        # Run sudo command - it will prompt for password itself
        result = subprocess.run(
            f"sudo {command}",
            shell=True,
            check=False,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Error running command: {e}")
        return False


def install_dependency(package: str, is_pip: bool = False) -> bool:
    """
    Install a single dependency.

    For pip packages: runs pip install directly.
    For apt packages: prompts for sudo and runs apt-get install.

    Args:
        package: Package name to install
        is_pip: True for pip package, False for apt package

    Returns:
        True if installation succeeded, False otherwise
    """
    if is_pip:
        # pip install doesn't need sudo (using user's venv)
        print(f"Installing Python package: {package}")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                print(f"  Successfully installed {package}")
                return True
            else:
                print(f"  Failed to install {package}: {result.stderr}")
                return False
        except Exception as e:
            print(f"  Error installing {package}: {e}")
            return False
    else:
        # apt-get needs sudo
        return prompt_for_sudo(f"apt-get install -y {package}")


def install_all_missing(deps: dict) -> dict:
    """
    Install all missing dependencies interactively.

    Args:
        deps: Output from check_dependencies()

    Returns:
        Dictionary with installation results:
        - success: bool - True if all installations succeeded
        - installed_apt: list - apt packages installed
        - installed_pip: list - pip packages installed
        - failed: list - packages that failed to install
    """
    result = {
        "success": True,
        "installed_apt": [],
        "installed_pip": [],
        "failed": [],
    }

    # Install apt packages first
    if deps.get("missing_apt"):
        print("\n=== System Packages (apt-get) ===")
        for package in deps["missing_apt"]:
            if install_dependency(package, is_pip=False):
                result["installed_apt"].append(package)
            else:
                result["failed"].append(package)
                result["success"] = False

    # Install pip packages
    if deps.get("missing_pip"):
        print("\n=== Python Packages (pip) ===")
        for package in deps["missing_pip"]:
            if install_dependency(package, is_pip=True):
                result["installed_pip"].append(package)
            else:
                result["failed"].append(package)
                result["success"] = False

    return result


def create_search_dir(base_path: str | Path) -> bool:
    """
    Create the SEARCH directory structure with all required files.

    Creates:
    - ./SEARCH/              - Main search directory
    - ./SEARCH/.vault.db     - SQLite FTS5 database (empty, initialized)
    - ./SEARCH/config.json   - Configuration settings
    - ./SEARCH/.vault-manifest.json - Index metadata
    - ./SEARCH/.searchignore - Exclusion patterns (optional)

    Args:
        base_path: The folder to create SEARCH directory in

    Returns:
        True if successful, False otherwise
    """
    base = Path(base_path)
    search_dir = base / "SEARCH"

    try:
        # Create SEARCH directory
        search_dir.mkdir(parents=True, exist_ok=True)

        # Create .vault.db (SQLite database with FTS5)
        db_path = search_dir / ".vault.db"
        if not db_path.exists():
            _init_search_db(db_path)

        # Create config.json (don't overwrite existing)
        config_path = search_dir / "config.json"
        if not config_path.exists():
            with open(config_path, "w") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)

        # Create .vault-manifest.json
        manifest_path = search_dir / ".vault-manifest.json"
        if not manifest_path.exists():
            manifest = {
                "file_count": 0,
                "content_hash": None,
                "last_index_time": None,
                "created_at": datetime.now().isoformat(),
                "version": "1.0.0",
            }
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

        # Create .searchignore
        ignore_path = search_dir / ".searchignore"
        if not ignore_path.exists():
            with open(ignore_path, "w") as f:
                f.write(DEFAULT_SEARCHIGNORE)

        return True

    except PermissionError as e:
        print(f"Permission denied creating search directory: {e}")
        return False
    except OSError as e:
        print(f"Error creating search directory: {e}")
        return False


def _init_search_db(db_path: Path) -> None:
    """
    Initialize the SQLite database with FTS5 schema.

    Creates minimal schema for folder search:
    - files: metadata about indexed files
    - files_fts: FTS5 virtual table for full-text search

    Args:
        db_path: Path to the database file
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Files table - metadata about indexed files
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            file_type TEXT,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            file_size INTEGER,
            modified_at TEXT,
            indexed_at TEXT NOT NULL
        )
    """)

    # FTS5 virtual table for full-text search
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
            file_path,
            filename,
            content,
            content='files',
            content_rowid='file_id',
            tokenize='porter unicode61'
        )
    """)

    # Triggers to keep FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
            INSERT INTO files_fts(rowid, file_path, filename, content)
            VALUES (NEW.file_id, NEW.file_path, NEW.filename, NEW.content);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
            INSERT INTO files_fts(files_fts, rowid, file_path, filename, content)
            VALUES ('delete', OLD.file_id, OLD.file_path, OLD.filename, OLD.content);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
            INSERT INTO files_fts(files_fts, rowid, file_path, filename, content)
            VALUES ('delete', OLD.file_id, OLD.file_path, OLD.filename, OLD.content);
            INSERT INTO files_fts(rowid, file_path, filename, content)
            VALUES (NEW.file_id, NEW.file_path, NEW.filename, NEW.content);
        END
    """)

    # Index metadata table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS index_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            file_count INTEGER,
            total_size INTEGER,
            status TEXT DEFAULT 'running'
        )
    """)

    # File sections table - pre-indexed section/date information
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_sections (
            section_id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER,
            section_date TEXT,
            section_header TEXT,
            section_type TEXT,
            FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
        )
    """)

    # Indexes for file_sections
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sections_file_line
        ON file_sections(file_id, line_start)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sections_date
        ON file_sections(section_date)
    """)

    # Schema version
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO schema_version VALUES (1)")

    conn.commit()
    conn.close()


def validate_setup(base_path: str | Path) -> dict:
    """
    Validate that the search directory is properly set up.

    Checks for:
    - SEARCH directory exists
    - .vault.db exists and is valid SQLite
    - config.json exists and is valid JSON
    - .vault-manifest.json exists

    Args:
        base_path: The folder containing SEARCH directory

    Returns:
        Dictionary with:
        - valid: bool - True if setup is complete
        - errors: list - List of error messages
        - warnings: list - List of warning messages
        - details: dict - Detailed status of each component
    """
    base = Path(base_path)
    search_dir = base / "SEARCH"

    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "details": {},
    }

    # Check SEARCH directory
    if not search_dir.is_dir():
        result["valid"] = False
        result["errors"].append("SEARCH directory missing")
        result["details"]["search_dir"] = {"exists": False}
        return result  # Can't check anything else

    result["details"]["search_dir"] = {"exists": True, "path": str(search_dir)}

    # Check .vault.db
    db_path = search_dir / ".vault.db"
    if not db_path.exists():
        result["valid"] = False
        result["errors"].append(".vault.db missing")
        result["details"]["database"] = {"exists": False}
    else:
        # Validate it's a proper SQLite database
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            check = cursor.fetchone()[0]
            conn.close()

            if check == "ok":
                result["details"]["database"] = {"exists": True, "valid": True}
            else:
                result["valid"] = False
                result["errors"].append(f".vault.db corrupted: {check}")
                result["details"]["database"] = {"exists": True, "valid": False}
        except Exception as e:
            result["valid"] = False
            result["errors"].append(f".vault.db error: {e}")
            result["details"]["database"] = {"exists": True, "valid": False}

    # Check config.json
    config_path = search_dir / "config.json"
    if not config_path.exists():
        result["valid"] = False
        result["errors"].append("config.json missing")
        result["details"]["config"] = {"exists": False}
    else:
        try:
            with open(config_path) as f:
                config = json.load(f)
            result["details"]["config"] = {
                "exists": True,
                "valid": True,
                "patterns_count": len(config.get("patterns", [])),
            }
        except json.JSONDecodeError as e:
            result["valid"] = False
            result["errors"].append(f"config.json invalid JSON: {e}")
            result["details"]["config"] = {"exists": True, "valid": False}

    # Check .vault-manifest.json
    manifest_path = search_dir / ".vault-manifest.json"
    if not manifest_path.exists():
        result["warnings"].append(".vault-manifest.json missing (will be created on index)")
        result["details"]["manifest"] = {"exists": False}
    else:
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            result["details"]["manifest"] = {
                "exists": True,
                "valid": True,
                "file_count": manifest.get("file_count", 0),
                "last_index": manifest.get("last_index_time"),
            }
        except json.JSONDecodeError as e:
            result["warnings"].append(f".vault-manifest.json invalid: {e}")
            result["details"]["manifest"] = {"exists": True, "valid": False}

    # Check .searchignore (optional)
    ignore_path = search_dir / ".searchignore"
    result["details"]["searchignore"] = {"exists": ignore_path.exists()}

    return result


def run_setup(base_path: str | Path, interactive: bool = True) -> dict:
    """
    Run complete setup process: check deps, install if needed, create directory.

    Args:
        base_path: The folder to set up search in
        interactive: If True, prompt for installations; if False, only report

    Returns:
        Dictionary with complete setup status
    """
    result = {
        "success": False,
        "dependencies": None,
        "installation": None,
        "directory": None,
        "validation": None,
    }

    # Check dependencies
    print("Checking dependencies...")
    deps = check_dependencies()
    result["dependencies"] = deps

    if not deps["all_satisfied"]:
        print(f"\nMissing dependencies:")
        if deps["missing_apt"]:
            print(f"  System packages: {', '.join(deps['missing_apt'])}")
        if deps["missing_pip"]:
            print(f"  Python packages: {', '.join(deps['missing_pip'])}")

        if interactive:
            install_result = install_all_missing(deps)
            result["installation"] = install_result

            if not install_result["success"]:
                print("\nSome dependencies failed to install.")
                return result
        else:
            print("\nRun with interactive=True to install missing dependencies.")
            return result
    else:
        print("All dependencies satisfied.")

    # Create directory structure
    print(f"\nCreating search directory in {base_path}...")
    if create_search_dir(base_path):
        result["directory"] = {"created": True, "path": str(Path(base_path) / "SEARCH")}
        print("Search directory created successfully.")
    else:
        result["directory"] = {"created": False}
        print("Failed to create search directory.")
        return result

    # Validate setup
    print("\nValidating setup...")
    validation = validate_setup(base_path)
    result["validation"] = validation

    if validation["valid"]:
        print("Setup complete and valid!")
        result["success"] = True
    else:
        print(f"Setup validation failed: {validation['errors']}")

    return result


# CLI entry point
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Set up portable folder search tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Check dependencies only
    python setup.py --check

    # Set up search in current directory
    python setup.py .

    # Set up search in specific folder
    python setup.py /path/to/folder

    # Non-interactive mode (just report, don't install)
    python setup.py --no-install /path/to/folder
        """,
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Folder to set up search in (default: current directory)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check dependencies, don't set up",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Don't prompt for installations, just report",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Only validate existing setup",
    )

    args = parser.parse_args()

    if args.check:
        # Just check dependencies
        deps = check_dependencies()
        print("\n=== Dependency Check ===")
        print(f"All satisfied: {deps['all_satisfied']}")
        if deps["missing_apt"]:
            print(f"Missing apt packages: {', '.join(deps['missing_apt'])}")
        if deps["missing_pip"]:
            print(f"Missing pip packages: {', '.join(deps['missing_pip'])}")
        print("\nDetails:")
        for name, status in deps["details"].items():
            installed = status.get("installed", False)
            symbol = "[OK]" if installed else "[MISSING]"
            print(f"  {symbol} {name}")
        sys.exit(0 if deps["all_satisfied"] else 1)

    elif args.validate:
        # Just validate existing setup
        validation = validate_setup(args.path)
        print("\n=== Setup Validation ===")
        print(f"Valid: {validation['valid']}")
        if validation["errors"]:
            print(f"Errors: {validation['errors']}")
        if validation["warnings"]:
            print(f"Warnings: {validation['warnings']}")
        sys.exit(0 if validation["valid"] else 1)

    else:
        # Full setup
        result = run_setup(args.path, interactive=not args.no_install)
        sys.exit(0 if result["success"] else 1)
