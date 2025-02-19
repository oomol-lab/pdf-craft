# how to release

```shell
pip install twine
```

```shell
python setup.py sdist bdist_wheel
```

```shell
twine upload dist/*
```