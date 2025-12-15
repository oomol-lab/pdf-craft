from .types import HTMLTag


def parse_raw_markdown(input: str) -> list[str | HTMLTag]:
    ...