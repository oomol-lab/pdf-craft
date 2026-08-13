# Development And Worktrees

**Scope:** setup, validation, VGE worktree behavior, cleanup, and release commands. **Not scope:** package architecture or conversion internals. **Read when:** choosing commands, editing project configuration, or changing development documentation.

## Environment

Use Python `>=3.11,<3.14`; Python 3.11 is the default development version. This repository uses Poetry 2.x and keeps the virtual environment inside the worktree when configured with `poetry config virtualenvs.in-project true`.

For ordinary development, install dependencies with:

```bash
poetry config virtualenvs.in-project true
poetry install --with dev
```

Install `torch` and `torchvision` separately only when a task needs import coverage or real OCR behavior for the selected environment.

## Default Validation

Prefer the smallest command set that crosses the changed boundary:

```bash
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
```

`poetry build` validates packaging. Full conversion scripts in `scripts/` are manual checks and may require Poppler, PyTorch, model cache, and CUDA.

## VGE Worktree Behavior

This project has no long-lived development service, so `.conductor/settings.toml` should not define `scripts.run` unless the project later gains a watcher or server. VGE setup should install dependencies only. Cleanup should remove worktree-local virtualenvs, test caches, build artifacts, and conversion outputs.

Generated conversion output should stay worktree-local under `analysing/`. Model caches may be large; use `models-cache/` only for deliberate local OCR work and avoid committing or assuming its contents.

## Human Documentation

Human-facing development instructions live in `docs/DEVELOPMENT.md` and `docs/DEVELOPMENT_zh-CN.md`. Keep README files focused on users of the library. Keep agent-facing routing and project constraints in `AGENTS.md` and this `references/` tree.

## Release

Release instructions live in `docs/RELEASE.md`. Do not change package version, dependency pins, or release metadata as part of ordinary worktree enablement unless the task explicitly asks for a release or dependency update.
