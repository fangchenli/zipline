from typing import Any

from numpy import ndarray

class AdjustedArrayWindow:
    def __init__(
        self,
        data: ndarray,
        view_kwargs: dict,
        adjustments: dict,
        offset: int,
        window_length: int,
        perspective_offset: int,
        rounding_places: int | None,
    ) -> None: ...
    def __iter__(self, /) -> Any: ...
    data: Any
    def seek(self, target_anchor: int) -> Any: ...
    view_kwargs: Any
    window_length: Any

class Exhausted(Exception): ...
