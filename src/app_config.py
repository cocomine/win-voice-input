import sys
from dataclasses import dataclass
from pathlib import Path


# Shared defaults live in one file so CLI, PowerShell wrappers, and tests do
# not drift apart. These values are behavior choices, not fallback paths.
DEFAULT_LANGUAGE = "yue-Hant-HK"
DEFAULT_RATE = 16000
DEFAULT_CHUNK_MS = 100
DEFAULT_FINAL_DEDUPE_SECONDS = 0.8
DEFAULT_IDLE_TIMEOUT_SECONDS = 5.0
DEFAULT_PLAY_STATUS_SOUNDS = True
DEFAULT_SHOW_LISTENING_INDICATOR = True
DEFAULT_LISTENING_INDICATOR_POSITION = "bottom-center"
ASSETS_DIR_NAME = "assets"
# The settings editor runs as a separate process when opened from the tray.
# Returning a dedicated non-error exit code lets the parent tray process know
# that config.json was saved and the main app should be restarted to reload it.
CONFIG_SAVED_RESTART_EXIT_CODE = 20
ALLOWED_LISTENING_INDICATOR_POSITIONS = (
    "bottom-center",
    "bottom-left",
    "bottom-right",
    "top-center",
    "top-left",
    "top-right",
)


def get_asset_dir() -> Path:
    # Source runs keep assets in the project-level assets folder, while
    # PyInstaller exposes bundled data under sys._MEIPASS. Centralizing this
    # path rule keeps tray icons, status sounds, and the listening indicator in
    # agreement after the folder restructure.
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / ASSETS_DIR_NAME
    return Path(__file__).resolve().parent.parent / ASSETS_DIR_NAME


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


@dataclass(frozen=True)
class FeedbackSettings:
    # Feedback settings control user cues around the dictation lifecycle. They
    # are separate from DictationSettings because they should not affect what
    # audio is sent to Google or what text is pasted.
    play_status_sounds: bool
    show_listening_indicator: bool
    listening_indicator_position: str
