# how to release

## 1. Configure PyPI token (first time only)

```shell
poetry config pypi-token.pypi $PYPI_TOKEN
```

## 2. Build the package

```shell
python build.py
```

## 3. Publish to PyPI

```shell
poetry publish
```