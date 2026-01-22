#!/usr/bin/env python3
"""
Similarity and Content Analysis - TF-IDF based file similarity and content analysis.

This module provides content analysis commands:
- related: Find similar files using TF-IDF cosine similarity
- duplicates: Find exact and near-duplicate content
- links: Extract and validate internal links (wiki-style and markdown)
- refs: Find symbol references with context

No external ML libraries required - uses pure Python TF-IDF implementation.
"""

import math
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


class SimilarityError(Exception):
    """Base exception for similarity module errors."""
    pass


class FileNotIndexedError(SimilarityError):
    """File not found in the index."""
    pass


class DatabaseNotFoundError(SimilarityError):
    """Database file does not exist."""
    pass


@contextmanager
def _get_connection(db_path: Path):
    """Context manager for database connections."""
    if not db_path.exists():
        raise DatabaseNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _tokenize(text: str) -> list[str]:
    """
    Tokenize text into lowercase words for TF-IDF.

    Removes punctuation and splits on whitespace.
    Filters out very short tokens and common stop words.

    Args:
        text: Input text to tokenize

    Returns:
        List of lowercase tokens
    """
    if not text:
        return []

    # Common English stop words to filter out
    stop_words = {
        'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
        'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'or', 'that',
        'the', 'to', 'was', 'were', 'will', 'with', 'this', 'but', 'they',
        'have', 'had', 'what', 'when', 'where', 'who', 'which', 'why', 'how',
        'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
        'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
        'than', 'too', 'very', 'can', 'just', 'should', 'now', 'also',
    }

    # Remove non-alphanumeric (keep spaces), convert to lowercase
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', ' ', text.lower())

    # Split and filter
    tokens = []
    for word in cleaned.split():
        if len(word) >= 2 and word not in stop_words:
            tokens.append(word)

    return tokens


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """
    Compute term frequency (TF) for a document.

    TF = count(term) / total_terms

    Args:
        tokens: List of tokens from the document

    Returns:
        Dictionary mapping term to its TF value
    """
    if not tokens:
        return {}

    counter = Counter(tokens)
    total = len(tokens)

    return {term: count / total for term, count in counter.items()}


def _compute_idf(documents: list[list[str]]) -> dict[str, float]:
    """
    Compute inverse document frequency (IDF) for all terms.

    IDF = log(total_docs / docs_containing_term)

    Args:
        documents: List of tokenized documents

    Returns:
        Dictionary mapping term to its IDF value
    """
    if not documents:
        return {}

    total_docs = len(documents)

    # Count documents containing each term
    doc_freq = Counter()
    for doc in documents:
        unique_terms = set(doc)
        doc_freq.update(unique_terms)

    # Compute IDF with smoothing to avoid division by zero
    idf = {}
    for term, count in doc_freq.items():
        # Add 1 to avoid log(1) = 0 for very common terms
        idf[term] = math.log(total_docs / count) + 1

    return idf


def _compute_tfidf_vector(tf: dict[str, float], idf: dict[str, float]) -> dict[str, float]:
    """
    Compute TF-IDF vector for a document.

    Args:
        tf: Term frequency dictionary for the document
        idf: Global IDF dictionary

    Returns:
        TF-IDF vector as dictionary
    """
    return {term: tf_val * idf.get(term, 1.0) for term, tf_val in tf.items()}


def _normalize_vector(vector: dict[str, float]) -> dict[str, float]:
    """
    Normalize a vector to unit length for cosine similarity.

    Args:
        vector: Input vector as dictionary

    Returns:
        Normalized vector
    """
    if not vector:
        return {}

    magnitude = math.sqrt(sum(v * v for v in vector.values()))

    if magnitude == 0:
        return {}

    return {k: v / magnitude for k, v in vector.items()}


def _cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """
    Compute cosine similarity between two normalized vectors.

    Args:
        vec1: First normalized vector
        vec2: Second normalized vector

    Returns:
        Cosine similarity (0.0 to 1.0)
    """
    if not vec1 or not vec2:
        return 0.0

    # Dot product of shared terms
    common_terms = set(vec1.keys()) & set(vec2.keys())

    if not common_terms:
        return 0.0

    return sum(vec1[term] * vec2[term] for term in common_terms)


def cmd_related(
    db_path: Path,
    filepath: str,
    top: int = 5,
) -> dict:
    """
    Find files similar to the target file using TF-IDF cosine similarity.

    Algorithm:
    1. Load all documents from the index
    2. Tokenize and compute TF for each document
    3. Compute global IDF across all documents
    4. Compute TF-IDF vectors and normalize
    5. Calculate cosine similarity between target and all others
    6. Return top N most similar files

    Args:
        db_path: Path to the SQLite database
        filepath: Path to the target file (can be relative or absolute)
        top: Number of similar files to return (default 5)

    Returns:
        Dictionary with:
        - target: Target file path
        - similar: List of similar files with scores
        - stats: Statistics about the comparison

    Raises:
        FileNotIndexedError: If target file is not in the index
        DatabaseNotFoundError: If database doesn't exist
    """
    # Normalize filepath - try both as-is and relative forms
    filepath_variants = [filepath]
    if filepath.startswith('/'):
        # Also try just the filename or relative path
        filepath_variants.append(Path(filepath).name)

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Get all files with content
        cursor.execute("SELECT file_id, file_path, content FROM files")
        rows = cursor.fetchall()

        if not rows:
            return {
                "target": filepath,
                "similar": [],
                "stats": {"total_files": 0, "error": "No files in index"},
            }

        # Build document collection
        documents = []  # List of (file_id, file_path, tokens)
        target_doc = None

        for row in rows:
            file_path = row["file_path"]
            content = row["content"] or ""
            tokens = _tokenize(content)

            doc_entry = {
                "file_id": row["file_id"],
                "file_path": file_path,
                "tokens": tokens,
            }
            documents.append(doc_entry)

            # Check if this is the target file
            if file_path == filepath or file_path in filepath_variants or filepath.endswith(file_path):
                target_doc = doc_entry

        # Try harder to find target file
        if target_doc is None:
            for doc in documents:
                if Path(doc["file_path"]).name == Path(filepath).name:
                    target_doc = doc
                    break

        if target_doc is None:
            raise FileNotIndexedError(f"File not found in index: {filepath}")

        if len(documents) < 2:
            return {
                "target": target_doc["file_path"],
                "similar": [],
                "stats": {"total_files": 1, "message": "Only one file in index"},
            }

        # Compute global IDF
        all_token_lists = [doc["tokens"] for doc in documents]
        idf = _compute_idf(all_token_lists)

        # Compute TF-IDF vectors for all documents
        vectors = {}
        for doc in documents:
            tf = _compute_tf(doc["tokens"])
            tfidf = _compute_tfidf_vector(tf, idf)
            vectors[doc["file_path"]] = _normalize_vector(tfidf)

        # Get target vector
        target_vector = vectors[target_doc["file_path"]]

        if not target_vector:
            return {
                "target": target_doc["file_path"],
                "similar": [],
                "stats": {
                    "total_files": len(documents),
                    "message": "Target file has no analyzable content",
                },
            }

        # Compute similarities
        similarities = []
        for doc in documents:
            if doc["file_path"] == target_doc["file_path"]:
                continue

            other_vector = vectors[doc["file_path"]]
            sim = _cosine_similarity(target_vector, other_vector)

            if sim > 0:
                similarities.append({
                    "file_path": doc["file_path"],
                    "similarity": round(sim, 4),
                })

        # Sort by similarity (highest first) and take top N
        similarities.sort(key=lambda x: -x["similarity"])
        top_similar = similarities[:top]

        return {
            "target": target_doc["file_path"],
            "similar": top_similar,
            "stats": {
                "total_files": len(documents),
                "files_with_similarity": len(similarities),
                "unique_terms": len(idf),
            },
        }


def cmd_duplicates(db_path: Path) -> dict:
    """
    Find duplicate and near-duplicate content in the index.

    Two types of duplicates are detected:
    1. Exact duplicates: Files with identical content_hash
    2. Near-duplicates: Files with 80%+ match in first 500 characters

    Args:
        db_path: Path to the SQLite database

    Returns:
        Dictionary with:
        - exact_duplicates: List of groups of exact duplicate file paths
        - near_duplicates: List of groups of near-duplicate files with similarity scores
        - stats: Statistics about duplicate detection

    Raises:
        DatabaseNotFoundError: If database doesn't exist
    """
    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Get all files with content hash and first 500 chars
        cursor.execute("""
            SELECT file_id, file_path, content_hash,
                   SUBSTR(content, 1, 500) as content_prefix
            FROM files
        """)
        rows = cursor.fetchall()

        if not rows:
            return {
                "exact_duplicates": [],
                "near_duplicates": [],
                "stats": {"total_files": 0},
            }

        # Group by content_hash for exact duplicates
        hash_groups: dict[str, list[str]] = {}
        files_data: list[dict] = []

        for row in rows:
            content_hash = row["content_hash"]
            file_path = row["file_path"]
            content_prefix = row["content_prefix"] or ""

            # Track for exact duplicates
            if content_hash not in hash_groups:
                hash_groups[content_hash] = []
            hash_groups[content_hash].append(file_path)

            # Store for near-duplicate detection
            files_data.append({
                "file_path": file_path,
                "content_hash": content_hash,
                "content_prefix": content_prefix,
            })

        # Extract exact duplicates (groups with more than one file)
        exact_duplicates = []
        exact_paths = set()
        for hash_val, paths in hash_groups.items():
            if len(paths) > 1:
                exact_duplicates.append({
                    "content_hash": hash_val,
                    "files": sorted(paths),
                    "count": len(paths),
                })
                exact_paths.update(paths)

        # Find near-duplicates (excluding exact duplicates)
        near_duplicates = []
        processed_pairs = set()

        for i, file1 in enumerate(files_data):
            if file1["file_path"] in exact_paths:
                continue

            for j, file2 in enumerate(files_data):
                if i >= j:
                    continue
                if file2["file_path"] in exact_paths:
                    continue

                # Skip if already an exact duplicate pair
                if file1["content_hash"] == file2["content_hash"]:
                    continue

                # Create a canonical pair key
                pair_key = tuple(sorted([file1["file_path"], file2["file_path"]]))
                if pair_key in processed_pairs:
                    continue

                # Compare content prefixes
                prefix1 = file1["content_prefix"]
                prefix2 = file2["content_prefix"]

                similarity = _compute_prefix_similarity(prefix1, prefix2)

                if similarity >= 0.80:
                    near_duplicates.append({
                        "files": [file1["file_path"], file2["file_path"]],
                        "similarity": round(similarity, 4),
                    })
                    processed_pairs.add(pair_key)

        # Sort near-duplicates by similarity (highest first)
        near_duplicates.sort(key=lambda x: -x["similarity"])

        # Calculate stats
        total_exact_dup_files = sum(len(group["files"]) for group in exact_duplicates)

        return {
            "exact_duplicates": exact_duplicates,
            "near_duplicates": near_duplicates,
            "stats": {
                "total_files": len(rows),
                "exact_duplicate_groups": len(exact_duplicates),
                "exact_duplicate_files": total_exact_dup_files,
                "near_duplicate_pairs": len(near_duplicates),
            },
        }


def _compute_prefix_similarity(text1: str, text2: str) -> float:
    """
    Compute similarity between two text prefixes using character-level comparison.

    Uses a simple approach: count matching characters at each position.

    Args:
        text1: First text
        text2: Second text

    Returns:
        Similarity score (0.0 to 1.0)
    """
    if not text1 and not text2:
        return 1.0
    if not text1 or not text2:
        return 0.0

    # Normalize whitespace for comparison
    text1 = ' '.join(text1.split())
    text2 = ' '.join(text2.split())

    if not text1 and not text2:
        return 1.0
    if not text1 or not text2:
        return 0.0

    # Use longer text as denominator
    max_len = max(len(text1), len(text2))
    min_len = min(len(text1), len(text2))

    # Count matching characters
    matches = 0
    for i in range(min_len):
        if text1[i] == text2[i]:
            matches += 1

    return matches / max_len


def cmd_links(db_path: Path) -> dict:
    """
    Extract and validate internal links from all indexed files.

    Scans for:
    - Wiki-style links: [[link]] and [[link|alias]]
    - Markdown links: [text](path.md) - local paths only (no http/https)

    For each link found, validates whether the target exists in the index.

    Args:
        db_path: Path to the SQLite database

    Returns:
        Dictionary with:
        - links: List of links with source, target, link_type, exists boolean
        - stats: Statistics about link validation

    Raises:
        DatabaseNotFoundError: If database doesn't exist
    """
    # Patterns for link extraction
    # Wiki-style: [[target]] or [[target|display]]
    wiki_pattern = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')

    # Markdown: [text](path) - exclude http/https URLs
    # Match [text](path) where path doesn't start with http:// or https://
    markdown_pattern = re.compile(r'\[([^\]]+)\]\((?!https?://)([^)]+)\)')

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Get all files and their content
        cursor.execute("SELECT file_path, content FROM files")
        rows = cursor.fetchall()

        # Build set of known file paths for validation
        cursor.execute("SELECT file_path FROM files")
        known_paths = {row["file_path"] for row in cursor.fetchall()}

        # Also build set of filenames without extension for wiki-style links
        known_names = set()
        for path in known_paths:
            name = Path(path).stem  # filename without extension
            known_names.add(name)
            known_names.add(Path(path).name)  # filename with extension

        links = []

        for row in rows:
            source_path = row["file_path"]
            content = row["content"] or ""

            # Find wiki-style links
            for match in wiki_pattern.finditer(content):
                target = match.group(1).strip()

                # Check if target exists
                exists = _check_link_exists(target, known_paths, known_names, source_path)

                links.append({
                    "source": source_path,
                    "target": target,
                    "link_type": "wiki",
                    "exists": exists,
                })

            # Find markdown links
            for match in markdown_pattern.finditer(content):
                target = match.group(2).strip()

                # Skip anchors and fragments
                if target.startswith('#'):
                    continue

                # Remove fragment from target for validation
                target_path = target.split('#')[0]

                if not target_path:
                    continue

                # Check if target exists
                exists = _check_link_exists(target_path, known_paths, known_names, source_path)

                links.append({
                    "source": source_path,
                    "target": target,
                    "link_type": "markdown",
                    "exists": exists,
                })

        # Calculate stats
        valid_links = sum(1 for link in links if link["exists"])
        broken_links = len(links) - valid_links

        return {
            "links": links,
            "stats": {
                "total_files": len(rows),
                "total_links": len(links),
                "valid_links": valid_links,
                "broken_links": broken_links,
                "wiki_links": sum(1 for link in links if link["link_type"] == "wiki"),
                "markdown_links": sum(1 for link in links if link["link_type"] == "markdown"),
            },
        }


def _check_link_exists(
    target: str,
    known_paths: set[str],
    known_names: set[str],
    source_path: str,
) -> bool:
    """
    Check if a link target exists in the index.

    Handles various forms:
    - Exact path match
    - Filename match (with or without extension)
    - Relative path from source file

    Args:
        target: The link target to check
        known_paths: Set of all indexed file paths
        known_names: Set of all indexed filenames
        source_path: Path of the source file containing the link

    Returns:
        True if target exists, False otherwise
    """
    # Direct match
    if target in known_paths:
        return True

    # Match by filename
    target_name = Path(target).name
    if target_name in known_names:
        return True

    # Match stem (without extension) for wiki-style links
    target_stem = Path(target).stem
    if target_stem in known_names:
        return True

    # Try relative path resolution
    source_dir = Path(source_path).parent
    resolved = source_dir / target
    resolved_str = str(resolved)

    if resolved_str in known_paths:
        return True

    # Normalize and try again
    try:
        normalized = str(Path(resolved_str))
        if normalized in known_paths:
            return True
    except Exception:
        pass

    return False


def cmd_refs(
    db_path: Path,
    symbol: str,
    context: int = 2,
) -> dict:
    """
    Find references to a symbol across all indexed files.

    Uses word boundary matching (\\b{symbol}\\b) for exact matches.
    More focused than general search - finds exact symbol occurrences.

    Args:
        db_path: Path to the SQLite database
        symbol: The symbol to search for (will be matched with word boundaries)
        context: Number of lines to show around each match (default 2)

    Returns:
        Dictionary with:
        - symbol: The searched symbol
        - references: List of references with file, line_number, context
        - stats: Statistics about the search

    Raises:
        DatabaseNotFoundError: If database doesn't exist
    """
    if not symbol or not symbol.strip():
        return {
            "symbol": symbol,
            "references": [],
            "stats": {"error": "Empty symbol provided"},
        }

    symbol = symbol.strip()

    # Build regex pattern with word boundaries
    # Escape special regex characters in symbol
    escaped_symbol = re.escape(symbol)
    pattern = re.compile(rf'\b{escaped_symbol}\b', re.IGNORECASE)

    with _get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Get all files with content
        cursor.execute("SELECT file_path, content FROM files")
        rows = cursor.fetchall()

        references = []
        files_with_refs = set()

        for row in rows:
            file_path = row["file_path"]
            content = row["content"] or ""

            if not content:
                continue

            lines = content.split('\n')

            for line_num, line in enumerate(lines, start=1):
                if pattern.search(line):
                    # Extract context
                    start_line = max(0, line_num - 1 - context)
                    end_line = min(len(lines), line_num + context)

                    context_lines = []
                    for ctx_line_num in range(start_line, end_line):
                        actual_line_num = ctx_line_num + 1
                        is_match_line = (actual_line_num == line_num)
                        context_lines.append({
                            "line_number": actual_line_num,
                            "content": lines[ctx_line_num],
                            "is_match": is_match_line,
                        })

                    references.append({
                        "file": file_path,
                        "line_number": line_num,
                        "line": line.strip(),
                        "context": context_lines,
                    })
                    files_with_refs.add(file_path)

        return {
            "symbol": symbol,
            "references": references,
            "stats": {
                "total_files_searched": len(rows),
                "files_with_references": len(files_with_refs),
                "total_references": len(references),
            },
        }


# Convenience function for command-line usage
def run_command(
    command: str,
    db_path: Path,
    **kwargs,
) -> dict:
    """
    Run a similarity command by name.

    Args:
        command: Command name ('related', 'duplicates', 'links', 'refs')
        db_path: Path to the SQLite database
        **kwargs: Command-specific arguments

    Returns:
        Command result dictionary

    Raises:
        ValueError: If command is unknown
    """
    commands = {
        "related": cmd_related,
        "duplicates": cmd_duplicates,
        "links": cmd_links,
        "refs": cmd_refs,
    }

    if command not in commands:
        raise ValueError(f"Unknown command: {command}. Available: {list(commands.keys())}")

    func = commands[command]

    # Filter kwargs to only include parameters the function accepts
    if command == "related":
        return func(db_path, kwargs.get("filepath", ""), kwargs.get("top", 5))
    elif command == "duplicates":
        return func(db_path)
    elif command == "links":
        return func(db_path)
    elif command == "refs":
        return func(db_path, kwargs.get("symbol", ""), kwargs.get("context", 2))

    return func(db_path, **kwargs)
