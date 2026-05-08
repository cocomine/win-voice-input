import time


class FinalTranscriptDeduper:
    # Google streaming can occasionally surface the same final transcript more
    # than once. The deduper is optional and time-bound so it can prevent double
    # paste without blocking legitimate repeated phrases later.
    def __init__(self, window_seconds: float):
        self.window_seconds = window_seconds
        self._last_key = ""
        self._last_time = 0.0

    def should_output(self, transcript: str) -> bool:
        if self.window_seconds <= 0:
            return True

        now = time.monotonic()
        # Whitespace normalization avoids treating formatting-only differences
        # as unique dictation results, while preserving punctuation differences.
        key = " ".join(transcript.split())
        if (
            key
            and key == self._last_key
            and now - self._last_time <= self.window_seconds
        ):
            return False

        self._last_key = key
        self._last_time = now
        return True
