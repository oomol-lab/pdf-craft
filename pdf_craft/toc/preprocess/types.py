from dataclasses import dataclass


@dataclass
class Toc:
    id: int
    page_index: int
    order: int
    children: list["Toc"]
