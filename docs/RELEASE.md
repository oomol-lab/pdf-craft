# Release

Releases are published by the manual GitHub Actions release workflow. Do not publish the package from a local machine.

## 1. Configure PyPI trusted publishing

This is required once for the PyPI project.

Configure the PyPI trusted publisher for this repository:

- PyPI project: `pdf-craft`
- GitHub owner: `oomol-lab`
- GitHub repository: `pdf-craft`
- Workflow: `release.yml`
- Environment: `pypi`

## 2. Prepare a release PR

For a target version such as `1.0.14`, prepare and merge a PR that includes:

- `pyproject.toml` version set to `1.0.14`
- `docs/changelog/v1.0.14.md` with the release notes

Use the existing files in `docs/changelog/` as the changelog style reference.

## 3. Run the release workflow

After the release PR is merged to `main`, open the GitHub Actions `Release` workflow and run it manually from `main`.

The workflow reads the version from `pyproject.toml`, checks that the repository is ready, builds the package, publishes it to PyPI, pushes the matching `vX.Y.Z` tag, and creates the GitHub Release using `docs/changelog/vX.Y.Z.md` as the release notes.
