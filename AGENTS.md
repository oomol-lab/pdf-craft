# Agent Workflow

pdf-craft is a Python library that converts scanned book PDFs into Markdown or EPUB. This repository uses `~/.agents/skills/vibecoding` as its general maintenance workflow. This file covers only pdf-craft-specific boundaries and task-based reading guidance.

## Workspace Boundaries

- `pdf_craft/` contains the package source. Public imports are exposed through `pdf_craft/__init__.py`, and convenience entry points are in `pdf_craft/functions.py`.
- `tests/` contains lightweight unit tests and small PDF fixtures. Use these tests as the default verification scope for ordinary code changes.
- `docs/` and `README.md` contain documentation for readers and contributors. Do not duplicate those instructions in agent documentation.
- `references/` contains agent-facing reference documents. Read only the references needed for the current task.
- `pdf_craft_tool/` is an unpublished local CLI for manual conversion, translation, and smoke-test matrices; `scripts/` contains only helpers for synchronizing dependency source code. Do not treat either as the default development workflow.
- `analysing/`, `pdf-craft-output/`, `models-cache/`, `.venv/`, `dist/`, `build/`, and `*.egg-info` contain generated or local runtime artifacts.

## Read Only When Needed

- When deciding module ownership, public API boundaries, or where new code belongs, read [Architecture and Module Boundaries](references/architecture.md).
- When changing PDF extraction, OCR normalization, cached XML artifacts, table-of-contents generation, chapter generation, Markdown rendering, or EPUB rendering, read [Conversion Pipeline](references/conversion-pipeline.md).
- When changing PDF text write-back, QTextLayout, font-size fitting, line-count or single-line rules, or PDF text-layer layout, read [Two-Stage PDF Text Layout](references/pdf-text-layout.md).
- When choosing setup, verification, worktree behavior, release procedures, or how to handle external dependencies, read [Development and Worktrees](references/development-and-worktrees.md).
- When preparing a release, updating version numbers, writing changelogs, or changing the release process, read [Release Workflow](references/release-workflow.md).

## Project-Specific Defaults

- This is a library project. Do not start a persistent development server unless a future task introduces a long-running service.
- Ordinary verification should avoid CUDA, model downloads, network requests, and full PDF conversions unless the task explicitly involves those behaviors.
- Never commit model caches or conversion outputs. By default, `pdf_craft_tool` writes its outputs to each worktree's own `pdf-craft-output/` directory; consider sharing an external model cache only when the task explicitly requires OCR.
- Documentation-only tasks must not change package code or dependency versions.
