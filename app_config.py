from dataclasses import dataclass


DEFAULT_LANGUAGE = "yue-Hant-HK"
DEFAULT_RATE = 16000
DEFAULT_CHUNK_MS = 100
DEFAULT_FINAL_DEDUPE_SECONDS = 0.8


@dataclass(frozen=True)
class AudioSettings:
    rate: int
    chunk_ms: int
    device: int | None


@dataclass(frozen=True)
class DictationSettings:
    paste_final: bool
    command_words: bool
    append_space: bool
    final_dedupe_seconds: float
