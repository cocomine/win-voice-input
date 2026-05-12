# Shared defaults live in one package so CLI, PowerShell wrappers, and tests do
# not drift apart. These values are behavior choices, not fallback paths.
DEFAULT_LANGUAGE = "yue-Hant-HK"
DEFAULT_RATE = 16000
DEFAULT_CHUNK_MS = 100
# Session-end preview paste defaults on because short utterances are the main
# failure case: Google may show useful interim text but never emit a final
# result before the session closes. Users can still disable it from Settings or
# the CLI when they prefer strict final-only output.
DEFAULT_PASTE_PREVIEW_ON_SESSION_END = True
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
