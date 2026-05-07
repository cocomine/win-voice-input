import argparse
import json
import os
import sys
from pathlib import Path

import sounddevice as sd

from app_config import (
    ALLOWED_LISTENING_INDICATOR_POSITIONS,
    DEFAULT_CHUNK_MS,
    DEFAULT_FINAL_DEDUPE_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_LISTENING_INDICATOR_POSITION,
    DEFAULT_PLAY_STATUS_SOUNDS,
    DEFAULT_RATE,
    DEFAULT_SHOW_LISTENING_INDICATOR,
    AudioSettings,
    DictationSettings,
    FeedbackSettings,
)
from dictation_session import listen
from global_hotkey import HOTKEY_DISPLAY_NAME
from hotkey_app import HotkeyDictationApp


def main() -> int:
    # voice_input.py is intentionally only the command-line entry point. Runtime
    # behavior is delegated to focused modules so code review can inspect audio,
    # STT, Windows output, and hotkey control independently.
    parser = argparse.ArgumentParser(
        description="Stream microphone audio to Google Speech-to-Text.",
        epilog=(
            f"Shortcut note: current builds use {HOTKEY_DISPLAY_NAME}; "
            "earlier test builds used Ctrl+Alt+Space."
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
        hotkey=None,
        tray=None,
        command_words=None,
        append_space=None,
    )
    args = parser.parse_args()

    if args.tray is True and args.hotkey is True:
        parser.error(
            "--tray and --hotkey cannot be used together because tray mode "
            f"already includes {HOTKEY_DISPLAY_NAME}."
        )

    if args.list_devices:
        # Listing devices must not create a Google client or touch Windows
        # clipboard state. It is a read-only diagnostic path for microphone setup.
        print(sd.query_devices())
        return 0

    # A packaged executable cannot rely on run-dictation.ps1 to read config.
    # Config is therefore loaded in the Python entry point as well. Missing
    # config.json is allowed and keeps these explicit built-in defaults.
    if args.config is None:
        if getattr(sys, "frozen", False):
            config_path = Path(sys.executable).resolve().parent / "config.json"
        else:
            config_path = Path(__file__).resolve().parent / "config.json"
        config_was_explicit = False
    else:
        config_path = Path(args.config).resolve()
        config_was_explicit = True

    settings = {
        "credentials": "",
        "device": None,
        "language": DEFAULT_LANGUAGE,
        "rate": DEFAULT_RATE,
        "chunk_ms": DEFAULT_CHUNK_MS,
        "paste_final": True,
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
            print(f"\nError: Failed to read config file: {config_path}", file=sys.stderr)
            print(str(exc), file=sys.stderr)
            return 1

        if not isinstance(config_data, dict):
            print(f"\nError: Config file must contain a JSON object: {config_path}", file=sys.stderr)
            return 1

        config_fields = {
            "credentials": "credentials",
            "device": "device",
            "language": "language",
            "rate": "rate",
            "chunkMs": "chunk_ms",
            "pasteFinal": "paste_final",
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

    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print(
            "\nError: Please set Google credentials in config.json or "
            "GOOGLE_APPLICATION_CREDENTIALS.",
            file=sys.stderr,
        )
        return 1

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
            # Tray mode owns its own UI loop and also starts a background global
            # hotkey listener. Importing here keeps non-tray commands usable
            # without loading tray-only dependencies.
            from tray_app import TrayDictationApp

            TrayDictationApp(
                str(settings["language"]),
                audio_settings,
                dictation_settings,
                feedback_settings,
            ).run()
        elif settings["hotkey"]:
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
            # Non-hotkey mode preserves the earlier prototype behavior: start a
            # listening session immediately, useful for quick console tests.
            listen(str(settings["language"]), audio_settings, dictation_settings)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
