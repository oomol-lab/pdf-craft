from dataclasses import dataclass

from .tags import HTMLTagDefinition


@dataclass
class HTMLTag:
    definition: HTMLTagDefinition
    attributes: list[tuple[str, str]]
    children: list["str | HTMLTag"]