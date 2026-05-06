from dataclasses import dataclass


# Shared defaults live in one file so CLI, PowerShell wrappers, and tests do
# not drift apart. These values are behavior choices, not fallback paths.
DEFAULT_LANGUAGE = "yue-Hant-HK"
DEFAULT_RATE = 16000
DEFAULT_CHUNK_MS = 100
DEFAULT_FINAL_DEDUPE_SECONDS = 0.8
DEFAULT_IDLE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class AudioSettings:
    # The audio capture layer receives this immutable object so it cannot
    # accidentally change microphone behavior while a Google stream is active.
    rate: int
    chunk_ms: int
    device: int | None


@dataclass(frozen=True)
class DictationSettings:
    # These settings describe output/session behavior that is independent from
    # the microphone. Keeping them separate from AudioSettings makes review
    # easier when tuning paste, command-word, or timeout behavior.
    paste_final: bool
    command_words: bool
    append_space: bool
    final_dedupe_seconds: float
    idle_timeout_seconds: float
