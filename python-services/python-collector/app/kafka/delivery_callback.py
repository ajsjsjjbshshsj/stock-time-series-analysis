from threading import Lock


class DeliveryCallback:
    """Thread-safe Kafka delivery result tracker."""

    def __init__(self):
        self._lock = Lock()
        self._success_count = 0
        self._failure_count = 0
        self._errors: list[str] = []

    def __call__(self, error, message):
        del message
        if error is None:
            with self._lock:
                self._success_count += 1
            return

        self.record_failure(error)

    def record_failure(self, error):
        with self._lock:
            self._failure_count += 1
            self._errors.append(str(error))

    @property
    def statistics(self) -> dict[str, object]:
        with self._lock:
            return {
                'successCount': self._success_count,
                'failureCount': self._failure_count,
                'errors': list(self._errors),
            }
