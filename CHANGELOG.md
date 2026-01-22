# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-01-22

Initial public release of **AI-grep**.

### Features

- **Full-text search** with SQLite FTS5 and ripgrep
- **Incremental indexing** - only re-indexes changed files
- **Multi-format support** - plain text, markdown, source code, .docx, .xlsx, .pdf
- **AI/LLM optimized** - commands designed for minimal token usage

### Commands

**Setup & Maintenance:** setup, status, index, validate, config

**Search & Retrieval:** search, get, bundle, context, list

**Analysis & Discovery:** stats, timeline, tags, outline, toc

**Content Analysis:** related, duplicates, links, refs

**Multi-Source:** mount, sources, unmount

**Export & Integration:** export, clip, open, history

**Search Enhancements:** diff, grep-context, relevant
