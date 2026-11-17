class ServiceError(Exception):
    """Only a stable code crosses the internal API boundary; never provider messages."""

    def __init__(self, code: str, status: int = 502):
        super().__init__(code)
        self.code = code
        self.status = status
