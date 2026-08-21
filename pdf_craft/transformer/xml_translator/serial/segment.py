from typing import Generic, Protocol, Self, TypeVar, runtime_checkable

S = TypeVar("S", covariant=True)
T = TypeVar("T")
ST = TypeVar("ST", bound="Segment")


@runtime_checkable
class Segment(Protocol, Generic[S]):
    @property
    def tokens(self) -> int: ...

    @property
    def payload(self) -> S: ...

    def truncate_after_head(self, remain_tokens: int) -> Self: ...
    def truncate_before_tail(self, remain_tokens: int) -> Self: ...
