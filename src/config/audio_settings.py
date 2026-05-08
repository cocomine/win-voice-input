from dataclasses import dataclass


@dataclass(frozen=True)
class AudioSettings:
    # The audio capture layer receives this immutable object so it cannot
    # accidentally change microphone behavior while a Google stream is active.
    rate: int
    chunk_ms: int
    device: int | None
