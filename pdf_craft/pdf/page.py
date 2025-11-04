from dataclasses import dataclass


ASSET_TAGS = ("image",)

@dataclass
class Page:
    index: int
    body_layouts: list["PageLayout"]
    footnotes_layouts: list["PageLayout"]

@dataclass
class PageLayout:
    ref: str
    det: tuple[int, int, int, int]
    text: str
    hash: str | None