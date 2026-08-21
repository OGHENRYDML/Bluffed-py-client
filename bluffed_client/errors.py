class BluffedError(Exception):
    pass


class TableError(BluffedError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StillWaitingAlone(BluffedError):
    """Raised by a fresh connect+sit that's been sitting in phase 'waiting'
    with nobody else around for too long — not a server error (seatPlayer
    accepted the sit fine), just a strong signal that assignTable's atomic
    reservation did its job (no double-booking) but still routed this
    connect to a table everyone else's near-simultaneous connects didn't
    land on. reset() catches this and tries a fresh connect instead of
    blocking the caller for the full step_timeout on a table that was never
    going to fill on its own."""
