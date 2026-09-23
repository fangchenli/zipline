from typing import Any

class AdjustedArrayWindow:
    def __iter__(self, /) -> Any: ...
    data: Any
    def seek(self, target_anchor: int) -> Any: ...
    view_kwargs: Any
    window_length: Any

class Exhausted(Exception): ...
