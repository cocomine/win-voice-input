from dataclasses import dataclass


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
