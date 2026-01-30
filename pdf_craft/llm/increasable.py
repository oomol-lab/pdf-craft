class Increaser:
    def __init__(self, value_range: tuple[float, float] | None):
        self._value_range: tuple[float, float] | None = value_range
        self._current: float | None = (
            value_range[0] if value_range is not None else None
        )

    @property
    def current(self) -> float | None:
        return self._current

    def increase(self):
        if self._value_range is not None and self._current is not None:
            _, end_value = self._value_range
            self._current = self._current + 0.5 * (end_value - self._current)


class Increasable:
    def __init__(self, param: float | tuple[float, float] | None):
        self._value_range: tuple[float, float] | None = None

        if isinstance(param, int):
            param = float(param)
        if isinstance(param, float):
            param = (param, param)
        if isinstance(param, (tuple, list)):
            if len(param) != 2:
                raise ValueError(f"Expected a tuple of length 2, got {len(param)}")
            begin, end = param
            if isinstance(begin, int):
                begin = float(begin)
            if isinstance(end, int):
                end = float(end)
            self._value_range = (begin, end)

    def context(self) -> Increaser:
        return Increaser(self._value_range)
