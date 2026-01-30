from dataclasses import dataclass
from enum import Enum, auto


@dataclass
class Message:
    role: "MessageRole"
    message: str


class MessageRole(Enum):
    SYSTEM = auto()
    USER = auto()
    ASSISTANT = auto()
