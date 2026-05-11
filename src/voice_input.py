import argparse
import json
import logging
import os
import stat
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import sounddevice as sd

from config import (
    ALLOWED_LISTENING_INDICATOR_POSITIONS,
    CONFIG_SAVED_RESTART_EXIT_CODE,
    DEFAULT_CHUNK_MS,
    DEFAULT_FINAL_DEDUPE_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_LISTENING_INDICATOR_POSITION,
    DEFAULT_PASTE_PREVIEW_ON_SESSION_END,
    DEFAULT_PLAY_STATUS_SOUNDS,
    DEFAULT_RATE,
    DEFAULT_SHOW_LISTENING_INDICATOR,
    AudioSettings,
    DictationSettings,
    FeedbackSettings,
)
# error_dialog is intentionally imported at startup rather than inside each
# error branch. It is a tiny Win32 wrapper, and windowed builds need it ready
# before config, logging, PySide6, or tray startup can fail silently.
from ui.error_dialog import (
    MESSAGE_BOX_RESULT_YES,
    MESSAGE_BOX_YES_NO,
    show_error_message,
)
from ui.global_hotkey_listener import HOTKEY_DISPLAY_NAME
from ui.hotkey_dictation_app import HotkeyDictationApp

IMMEDIATE_MODE_STATUS_POLL_SECONDS = 0.1
LOG_FOLDER_NAME = "WinVoiceInput"
LOG_FILE_NAME = "win-voice-input.log"
LOG_MAX_BYTES = 1_048_576
LOG_BACKUP_COUNT = 5


def main() -> int:
    # voice_input.py is intentionally only the command-line entry point. Runtime
    # behavior is delegated to focused modules so code review can inspect audio,
    # STT, Windows output, and hotkey control independently.
    parser = argparse.ArgumentParser(
        description="Stream microphone audio to Google Speech-to-Text.",
        epilog=(
            f"Shortcut note: current builds use {HOTKEY_DISPLAY_NAME}; this "
            "restores the original shortcut after a short Ctrl+Alt+V test build."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.json. Default: config.json next to the app.",
    )
    parser.add_argument(
        "--language",
        default=None,
        help=f"BCP-47 language code. Default: {DEFAULT_LANGUAGE}",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=None,
        help=f"Microphone sample rate. Default: {DEFAULT_RATE}",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=None,
        help=f"Audio chunk size in milliseconds. Default: {DEFAULT_CHUNK_MS}",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Input device index. Use --list-devices to find one.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Show audio devices and exit.",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Open the config editor, wait until it closes, then exit.",
    )
    parser.add_argument(
        "--paste-final",
        dest="paste_final",
        action="store_true",
        default=None,
        help="Paste final transcripts into the active app with Ctrl+V.",
    )
    parser.add_argument(
        "--no-paste-final",
        dest="paste_final",
        action="store_false",
        help="Do not paste final transcripts into the active app.",
    )
    parser.add_argument(
        "--paste-preview-on-session-end",
        dest="paste_preview_on_session_end",
        action="store_true",
        default=None,
        help=(
            "Paste the latest non-final preview if the listening session ends "
            "before Google returns a final transcript."
        ),
    )
    parser.add_argument(
        "--no-paste-preview-on-session-end",
        dest="paste_preview_on_session_end",
        action="store_false",
        help="Do not paste pending preview text when a listening session ends.",
    )
    parser.add_argument(
        "--hotkey",
        dest="hotkey",
        action="store_true",
        default=None,
        help=(
            "Use console hotkey mode. Tray mode already includes "
            f"{HOTKEY_DISPLAY_NAME}, so pass --hotkey without --tray to "
            "select the non-tray UI."
        ),
    )
    parser.add_argument(
        "--no-hotkey",
        dest="hotkey",
        action="store_false",
        help=f"Do not use global {HOTKEY_DISPLAY_NAME} hotkey mode.",
    )
    parser.add_argument(
        "--tray",
        dest="tray",
        action="store_true",
        default=None,
        help=(
            "Show a system tray icon with status, Start/Pause/Exit controls, "
            f"and {HOTKEY_DISPLAY_NAME}."
        ),
    )
    parser.add_argument(
        "--no-tray",
        dest="tray",
        action="store_false",
        help="Do not show a system tray icon.",
    )
    parser.add_argument(
        "--command-words",
        dest="command_words",
        action="store_true",
        default=None,
        help="Enable command words like 換行, 逗號, 句號, 刪除.",
    )
    parser.add_argument(
        "--no-command-words",
        dest="command_words",
        action="store_false",
        help="Keep command words disabled. This is the default.",
    )
    parser.add_argument(
        "--append-space",
        dest="append_space",
        action="store_true",
        default=None,
        help="Append a space after pasted final text when it has no ending punctuation.",
    )
    parser.add_argument(
        "--no-append-space",
        dest="append_space",
        action="store_false",
        help="Do not append a space after pasted final text.",
    )
    parser.add_argument(
        "--final-dedupe-seconds",
        type=float,
        default=None,
        help=(
            "Skip pasting the same final transcript again inside this many "
            f"seconds. Default: {DEFAULT_FINAL_DEDUPE_SECONDS}"
        ),
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=float,
        default=None,
        help=(
            "Stop the current listening session after this many seconds with "
            f"no recognized text. Use 0 to disable. Default: {DEFAULT_IDLE_TIMEOUT_SECONDS}"
        ),
    )
    # Boolean options are tri-state here: None means "use config/default",
    # True/False means an explicit CLI override. This is important for packaged
    # exe use, where config.json should control daily behavior.
    parser.set_defaults(
        paste_final=None,
        paste_preview_on_session_end=None,
        hotkey=None,
        tray=None,
        command_words=None,
        append_space=None,
    )
    args = parser.parse_args()

    if args.tray is True and args.hotkey is True:
        message = (
            "--tray and --hotkey cannot be used together because tray mode "
            f"already includes {HOTKEY_DISPLAY_NAME}."
        )
        show_error_message("Win Voice Input Arguments Error", message)
        parser.error(message)

    if args.list_devices:
        # Listing devices must not create a Google client or touch Windows
        # clipboard state. It is a read-only diagnostic path for microphone setup,
        # so it also avoids creating app log files as a side effect.
        print(sd.query_devices())
        return 0

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        show_error_message(
            "Win Voice Input Error",
            "LOCALAPPDATA is not set, so the app cannot create logs.",
        )
        print(
            "\nError: LOCALAPPDATA is not set, so the app cannot create logs.",
            file=sys.stderr,
        )
        return 1

    log_dir = Path(local_app_data) / LOG_FOLDER_NAME / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        show_error_message(
            "Win Voice Input Error",
            f"Failed to create log folder:\n{log_dir}\n\n{exc}",
        )
        print(f"\nError: Failed to create log folder: {log_dir}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    # Windowed builds have no console, so file logging is initialized before
    # config and runtime setup. Existing print() calls remain the test-build
    # console output, while the rotating file keeps daily logs bounded.
    log_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    file_handler = RotatingFileHandler(
        log_dir / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(log_formatter)
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    logging.info("Logging started: %s", log_dir / LOG_FILE_NAME)

    # A packaged executable cannot rely on run-dictation.ps1 to read config.
    # Config is therefore loaded in the Python entry point as well. Missing
    # config.json is allowed and keeps these explicit built-in defaults.
    if args.config is None:
        if getattr(sys, "frozen", False):
            config_path = Path(sys.executable).resolve().parent / "config.json"
        else:
            # Source code now lives in src/, while local config remains at the
            # project root beside the PowerShell scripts and README.
            config_path = Path(__file__).resolve().parent.parent / "config.json"
        config_was_explicit = False
    else:
        config_path = Path(args.config).resolve()
        config_was_explicit = True
    logging.info("Using config path: %s", config_path)

    if args.settings:
        try:
            from ui.config_editor_window import run_config_editor

            return run_config_editor(config_path)
        except Exception as exc:
            message = (
                "Unable to open the settings editor.\n\n"
                f"{exc}\n\n"
                "If this is a source run, install runtime dependencies from "
                "requirements.txt."
            )
            logging.exception("Failed to open config editor.")
            show_error_message("Win Voice Input Settings Error", message)
            print(f"\nError: {message}", file=sys.stderr)
            return 1

    def show_setup_error_and_maybe_open_settings(
        title: str,
        message: str,
        console_message: str,
    ) -> int:
        # Credential setup errors happen before tray startup, so a windowed exe
        # would otherwise exit without giving the user a direct path to fix
        # config.json. The same Settings editor is opened only when the user
        # chooses Yes; after it closes, the app exits so the next launch can
        # reload the saved credentials cleanly.
        logging.error(console_message)
        response = show_error_message(
            title,
            (
                f"{message}\n\n"
                "Open Settings now?\n\n"
                "Choose Yes to select the Google credentials JSON, then "
                "restart Win Voice Input after saving."
            ),
            MESSAGE_BOX_YES_NO,
        )
        print(f"\nError: {console_message}", file=sys.stderr)
        if response != MESSAGE_BOX_RESULT_YES:
            return 1

        try:
            from ui.config_editor_window import run_config_editor

            settings_result = run_config_editor(config_path)
            # The settings editor is a recovery path for startup setup errors.
            # When it closes successfully, the app exits successfully as well;
            # the next launch reloads config.json and starts tray/hotkey mode
            # with the corrected credentials.
            if settings_result == CONFIG_SAVED_RESTART_EXIT_CODE:
                # In the startup recovery path there is no tray parent process
                # watching the Settings editor. Launch the main app explicitly
                # after a successful save so first-time setup can continue
                # without asking the user to start the program again.
                if getattr(sys, "frozen", False):
                    restart_command = [
                        sys.executable,
                        "--config",
                        str(config_path),
                    ]
                else:
                    restart_command = [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--config",
                        str(config_path),
                    ]
                import subprocess

                try:
                    subprocess.Popen(restart_command)
                    logging.info(
                        "Restarted Win Voice Input after setup settings save."
                    )
                    return 0
                except OSError as exc:
                    logging.exception(
                        "Failed to restart after setup settings save."
                    )
                    show_error_message(
                        "Win Voice Input Restart Error",
                        "Settings were saved, but the app could not restart:\n\n"
                        f"{exc}",
                    )
                    print(
                        f"\nError: Failed to restart Win Voice Input: {exc}",
                        file=sys.stderr,
                    )
                    return 1
            return settings_result
        except Exception as exc:
            settings_message = (
                "Unable to open the settings editor.\n\n"
                f"{exc}\n\n"
                "If this is a source run, install runtime dependencies from "
                "requirements.txt."
            )
            logging.exception("Failed to open config editor after setup error.")
            show_error_message("Win Voice Input Settings Error", settings_message)
            print(f"\nError: {settings_message}", file=sys.stderr)
            return 1

    settings = {
        "credentials": "",
        "device": None,
        "language": DEFAULT_LANGUAGE,
        "rate": DEFAULT_RATE,
        "chunk_ms": DEFAULT_CHUNK_MS,
        "paste_final": True,
        "paste_preview_on_session_end": DEFAULT_PASTE_PREVIEW_ON_SESSION_END,
        "tray": True,
        "hotkey": True,
        "command_words": False,
        "append_space": False,
        "final_dedupe_seconds": DEFAULT_FINAL_DEDUPE_SECONDS,
        "idle_timeout_seconds": DEFAULT_IDLE_TIMEOUT_SECONDS,
        "play_status_sounds": DEFAULT_PLAY_STATUS_SOUNDS,
        "show_listening_indicator": DEFAULT_SHOW_LISTENING_INDICATOR,
        "listening_indicator_position": DEFAULT_LISTENING_INDICATOR_POSITION,
    }

    if config_path.exists():
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logging.exception("Failed to read config file: %s", config_path)
            show_error_message(
                "Win Voice Input Config Error",
                f"Failed to read config file:\n{config_path}\n\n{exc}",
            )
            print(f"\nError: Failed to read config file: {config_path}", file=sys.stderr)
            print(str(exc), file=sys.stderr)
            return 1

        if not isinstance(config_data, dict):
            logging.error("Config file is not a JSON object: %s", config_path)
            show_error_message(
                "Win Voice Input Config Error",
                f"Config file must contain a JSON object:\n{config_path}",
            )
            print(f"\nError: Config file must contain a JSON object: {config_path}", file=sys.stderr)
            return 1

        config_fields = {
            "credentials": "credentials",
            "device": "device",
            "language": "language",
            "rate": "rate",
            "chunkMs": "chunk_ms",
            "pasteFinal": "paste_final",
            "pastePreviewOnSessionEnd": "paste_preview_on_session_end",
            "tray": "tray",
            "hotkey": "hotkey",
            "commandWords": "command_words",
            "appendSpace": "append_space",
            "finalDedupeSeconds": "final_dedupe_seconds",
            "idleTimeoutSeconds": "idle_timeout_seconds",
            "playStatusSounds": "play_status_sounds",
            "showListeningIndicator": "show_listening_indicator",
            "listeningIndicatorPosition": "listening_indicator_position",
        }
        for config_name, setting_name in config_fields.items():
            if config_name in config_data:
                settings[setting_name] = config_data[config_name]
    elif config_was_explicit:
        logging.error("Config file does not exist: %s", config_path)
        show_error_message(
            "Win Voice Input Config Error",
            f"Config file does not exist:\n{config_path}",
        )
        print(f"\nError: Config file does not exist: {config_path}", file=sys.stderr)
        return 1

    if settings["credentials"]:
        credentials_path = Path(str(settings["credentials"]))
        if not credentials_path.is_absolute():
            # Relative credential paths are resolved against config.json, not the
            # current shell directory. This makes double-clicked exe startup and
            # PowerShell startup behave consistently.
            credentials_path = config_path.parent / credentials_path
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)

    credentials_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_env:
        return show_setup_error_and_maybe_open_settings(
            "Win Voice Input Setup Error",
            "Google credentials are not configured.\n\n"
            "Please set Google credentials in config.json or "
            "GOOGLE_APPLICATION_CREDENTIALS.",
            "Google credentials are not configured.",
        )

    credentials_file = Path(credentials_env)
    try:
        credentials_stat = credentials_file.stat()
    except FileNotFoundError:
        # This is intentionally a hard startup check, not a fallback. Without a
        # readable service account JSON file the first listening session cannot
        # create a Google STT stream, so starting tray/hotkey UI would only
        # delay the same failure until the user presses Start.
        return show_setup_error_and_maybe_open_settings(
            "Win Voice Input Setup Error",
            "Google service account key file was not found:\n"
            f"{credentials_file}\n\n"
            "Please select the correct JSON key in Settings, edit config.json, "
            "or update GOOGLE_APPLICATION_CREDENTIALS.",
            f"Google credentials file does not exist: {credentials_file}",
        )
    except OSError as exc:
        # The service account key is required before microphone, tray, or
        # Google STT startup. Checking it here turns an otherwise delayed Google
        # client failure into a clear startup message for windowed builds.
        logging.exception("Failed to inspect Google credentials file.")
        return show_setup_error_and_maybe_open_settings(
            "Win Voice Input Setup Error",
            "Unable to check the Google service account key file:\n"
            f"{credentials_file}\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Please verify the credentials path in config.json or "
            "GOOGLE_APPLICATION_CREDENTIALS.",
            "Unable to check the Google service account key file: "
            f"{credentials_file}. {type(exc).__name__}: {exc}",
        )

    if not stat.S_ISREG(credentials_stat.st_mode):
        # Google authentication expects a service account JSON file, not a
        # folder or special device path. Reporting this before startup prevents
        # a delayed Google client error after the user presses the hotkey.
        return show_setup_error_and_maybe_open_settings(
            "Win Voice Input Setup Error",
            "Google service account key path is not a file:\n"
            f"{credentials_file}\n\n"
            "Please select a JSON key file in Settings, edit config.json, "
            "or update GOOGLE_APPLICATION_CREDENTIALS.",
            f"Google credentials path is not a file: {credentials_file}",
        )

    if args.language is not None:
        settings["language"] = args.language
    if args.rate is not None:
        settings["rate"] = args.rate
    if args.chunk_ms is not None:
        settings["chunk_ms"] = args.chunk_ms
    if args.device is not None:
        settings["device"] = args.device
    if args.paste_final is not None:
        settings["paste_final"] = args.paste_final
    if args.paste_preview_on_session_end is not None:
        settings["paste_preview_on_session_end"] = (
            args.paste_preview_on_session_end
        )
    if args.tray is not None:
        settings["tray"] = args.tray
    if args.hotkey is not None:
        settings["hotkey"] = args.hotkey
        if args.hotkey and args.tray is None:
            # Tray mode already includes the global hotkey. An explicit
            # --hotkey without --tray means the user selected the older console
            # hotkey UI, so tray is turned off at the command-line boundary.
            settings["tray"] = False
    if args.command_words is not None:
        settings["command_words"] = args.command_words
    if args.append_space is not None:
        settings["append_space"] = args.append_space
    if args.final_dedupe_seconds is not None:
        settings["final_dedupe_seconds"] = args.final_dedupe_seconds
    if args.idle_timeout_seconds is not None:
        settings["idle_timeout_seconds"] = args.idle_timeout_seconds

    listening_indicator_position = str(
        settings["listening_indicator_position"]
    ).strip().lower()
    if listening_indicator_position not in ALLOWED_LISTENING_INDICATOR_POSITIONS:
        allowed_positions = ", ".join(ALLOWED_LISTENING_INDICATOR_POSITIONS)
        logging.error(
            "Invalid listeningIndicatorPosition: %r",
            settings["listening_indicator_position"],
        )
        show_error_message(
            "Win Voice Input Config Error",
            "Invalid listeningIndicatorPosition in config.json:\n"
            f"{settings['listening_indicator_position']!r}\n\n"
            f"Expected one of: {allowed_positions}.",
        )
        print(
            "\nError: Invalid listeningIndicatorPosition in config.json: "
            f"{settings['listening_indicator_position']!r}. Expected one of: "
            f"{allowed_positions}.",
            file=sys.stderr,
        )
        return 1

    audio_settings = AudioSettings(
        rate=int(settings["rate"]),
        chunk_ms=int(settings["chunk_ms"]),
        # sounddevice accepts None for "use the Windows default input device"
        # and an integer index for an explicit microphone. Config JSON can carry
        # either shape, so conversion happens at the boundary before audio code
        # receives the setting.
        device=None if settings["device"] is None else int(settings["device"]),
    )
    dictation_settings = DictationSettings(
        paste_final=bool(settings["paste_final"]),
        # paste_final controls normal Google final commits during the live
        # stream. paste_preview_on_session_end is intentionally separate: it
        # only salvages the latest non-final preview after the session is
        # already ending, so users can enable short-utterance recovery without
        # changing whether normal final transcripts are pasted.
        paste_preview_on_session_end=bool(
            settings["paste_preview_on_session_end"]
        ),
        command_words=bool(settings["command_words"]),
        append_space=bool(settings["append_space"]),
        final_dedupe_seconds=float(settings["final_dedupe_seconds"]),
        idle_timeout_seconds=float(settings["idle_timeout_seconds"]),
    )
    feedback_settings = FeedbackSettings(
        # Feedback settings are read from config.json only for now. Keeping them
        # out of the PowerShell flags avoids changing the existing launch
        # surface while still letting packaged exe users tune daily behavior.
        play_status_sounds=bool(settings["play_status_sounds"]),
        show_listening_indicator=bool(settings["show_listening_indicator"]),
        listening_indicator_position=listening_indicator_position,
    )

    try:
        if settings["tray"]:
            logging.info("Starting tray mode.")
            # Tray mode owns its own UI loop and also starts a background global
            # hotkey listener. Importing here keeps non-tray commands usable
            # without loading tray-only dependencies.
            from ui.tray_dictation_app import TrayDictationApp

            TrayDictationApp(
                str(settings["language"]),
                audio_settings,
                dictation_settings,
                feedback_settings,
                config_path,
                log_dir,
            ).run()
        elif settings["hotkey"]:
            logging.info("Starting console hotkey mode.")
            # Hotkey mode starts idle and creates a listening session only after
            # the configured shortcut. This avoids recording or billing while
            # the user is not actively dictating.
            HotkeyDictationApp(
                str(settings["language"]),
                audio_settings,
                dictation_settings,
                feedback_settings,
            ).run()
        else:
            logging.info("Starting immediate listening mode.")
            # Non-hotkey mode still starts immediately, but it uses the same
            # controller as tray/hotkey mode so config-driven feedback settings
            # such as status sounds and the listening indicator stay consistent.
            from dictation.dictation_controller import DictationController

            listening_indicator = None
            if feedback_settings.show_listening_indicator:
                from ui.listening_indicator import ListeningIndicator

                listening_indicator = ListeningIndicator(
                    feedback_settings.listening_indicator_position
                )

            def on_immediate_status_change(status: str) -> None:
                # DictationController reports every lifecycle transition through
                # this callback. Immediate mode has no tray menu to mirror that
                # state, so the console and optional indicator are updated here.
                print(f"Status: {status}")
                if listening_indicator is None:
                    return
                if status == "Listening":
                    listening_indicator.set_text("")
                    listening_indicator.show()
                else:
                    listening_indicator.set_text("")
                    listening_indicator.hide()

            def on_immediate_recognition_text(text: str) -> None:
                # Immediate mode has no tray object, but it can still show the
                # same overlay-only interim preview as tray and hotkey modes.
                if listening_indicator is not None:
                    listening_indicator.set_text(text)

            controller = DictationController(
                str(settings["language"]),
                audio_settings,
                dictation_settings,
                feedback_settings,
                on_immediate_status_change,
                on_immediate_recognition_text,
            )
            try:
                controller.start()
                while controller.status != "Idle":
                    # Immediate mode has no tray or hotkey event loop to block
                    # on. A short named sleep keeps Ctrl+C responsive while the
                    # worker thread owns microphone and Google streaming work.
                    time.sleep(IMMEDIATE_MODE_STATUS_POLL_SECONDS)
            finally:
                controller.shutdown()
                if listening_indicator is not None:
                    listening_indicator.shutdown()
    except KeyboardInterrupt:
        logging.info("Stopped by KeyboardInterrupt.")
        print("\nStopped.")
        return 0
    except Exception as exc:
        logging.exception("Unhandled application error.")
        show_error_message("Win Voice Input Error", str(exc))
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
