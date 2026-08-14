# Release Workflow

**Scope:** release preparation, version alignment, changelog expectations, and the repository state required by the manual GitHub Actions release workflow. **Out of scope:** conversion internals, dependency upgrades unrelated to a release, and manual PyPI publishing.

## Release Model

Releases are executed by the manual GitHub Actions release workflow. The repository must be prepared before that workflow runs. The workflow is the release gate: it checks that the requested version matches the repository state, publishes the package to PyPI, pushes the version tag, and creates the GitHub Release using the changelog file as the release notes.

Agent work should focus on preparing the release prerequisites in the repository. Do not publish to PyPI manually, create release tags manually, or create GitHub Releases manually as part of release preparation.

## Preparing a Release

For a target version `X.Y.Z`, prepare the repository so these facts are true:

- [pyproject.toml](../pyproject.toml) has `tool.poetry.version = "X.Y.Z"`.
- `docs/changelog/vX.Y.Z.md` exists and contains the release notes.
- The changelog includes a concise summary, grouped changes when useful, linked PRs/issues when available, and a full changelog compare link.
- The release preparation changes are reviewed through the normal PR path before the manual release workflow is run from `main`.

Keep the release preparation PR focused. Do not combine release preparation with unrelated refactors or dependency changes unless the release is specifically about those changes.

## Validation

Use the normal lightweight validation surface for release preparation:

```bash
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
poetry build
```

If a validation step cannot be run locally, state that clearly in the handoff.

## Changelog Notes

The GitHub Release body is read directly from `docs/changelog/vX.Y.Z.md` by the release workflow. Write changelog files as reader-facing release notes, not as internal implementation notes.

Use the existing changelog files in `docs/changelog/` as the style reference. The release title and tag use the `vX.Y.Z` form.
