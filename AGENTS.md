# Agent Workflow

pdf-craft is a Python library for converting scanned-book PDFs to Markdown or EPUB. This repository uses `~/.agents/skills/vibecoding` as the general maintenance workflow. This file only records pdf-craft-specific boundaries and lazy reference routing.

## Workspace Boundaries

- `pdf_craft/` is the package source. Public imports are exposed from `pdf_craft/__init__.py` and helper entry points in `pdf_craft/functions.py`.
- `tests/` contains lightweight unit tests and small PDF fixtures. These tests are the default validation surface for ordinary code changes.
- `docs/`, `README.md`, and `README_zh-CN.md` are human-facing documentation. Do not duplicate those explanations here.
- `references/` is agent-facing documentation. Read only the reference required by the current task.
- `scripts/` contains local manual conversion and dependency-sync helpers. Do not treat those scripts as the default development workflow.
- `analysing/`, `models-cache/`, `.venv/`, `dist/`, `build/`, and `*.egg-info` are generated or local runtime artifacts.

## Read Only When Needed

- When deciding module ownership, public API boundaries, or where code belongs, read [Architecture](references/architecture.md).
- When modifying PDF extraction, OCR normalization, cached XML artifacts, TOC generation, chapter generation, Markdown rendering, or EPUB rendering, read [Conversion Pipeline](references/conversion-pipeline.md).
- When choosing setup, validation, worktree, cleanup, release, or external dependency behavior, read [Development And Worktrees](references/development-and-worktrees.md).

## Project Defaults

- This is a library project. Do not start a long-lived dev server unless a future task introduces one.
- Ordinary validation should avoid CUDA, model downloads, network calls, and full PDF conversion unless the task explicitly touches that behavior.
- Keep model caches and conversion outputs outside committed source. In VGE worktrees, prefer per-worktree `analysing/` output and shared external model caches only when the task explicitly needs OCR.
- Pure documentation tasks should not modify package code or dependency versions.
