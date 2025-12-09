from dataclasses import dataclass



@dataclass
class TocItem:
    id: int
    title: str
    children: list["TocItem"]
