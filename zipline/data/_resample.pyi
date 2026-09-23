from numpy import ndarray

def _minute_to_session_close(
    close_locs: ndarray, data: ndarray, out: ndarray
) -> None: ...
def _minute_to_session_high(
    close_locs: ndarray, data: ndarray, out: ndarray
) -> None: ...
def _minute_to_session_low(
    close_locs: ndarray, data: ndarray, out: ndarray
) -> None: ...
def _minute_to_session_open(
    close_locs: ndarray, data: ndarray, out: ndarray
) -> None: ...
def _minute_to_session_volume(
    close_locs: ndarray, data: ndarray, out: ndarray
) -> None: ...

nan: float
