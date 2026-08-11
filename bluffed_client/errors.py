class BluffedError(Exception):
    pass


class TableError(BluffedError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
