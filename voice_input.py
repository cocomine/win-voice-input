import argparse
import sys

import sounddevice as sd

from app_config import (
    DEFAULT_CHUNK_MS,
    DEFAULT_FINAL_DEDUPE_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_RATE,
    AudioSettings,
    DictationSettings,
)
from dictation_session import listen
from hotkey_app import HotkeyDictationApp


def main() -> int:
    # voice_input.py is intentionally only the command-line entry point. Runtime
    # behavior is delegated to focused modules so code review can inspect audio,
    # STT, Windows output, and hotkey control independently.
    parser = argparse.ArgumentParser(
        description="Stream microphone audio to Google Speech-to-Text."
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help=f"BCP-47 language code. Default: {DEFAULT_LANGUAGE}",
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=DEFAULT_RATE,
        help=f"Microphone sample rate. Default: {DEFAULT_RATE}",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=DEFAULT_CHUNK_MS,
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
        action="store_true",
        help="Paste final transcripts into the active app with Ctrl+V.",
    )
    parser.add_argument(
        "--hotkey",
        action="store_true",
        help="Start in idle mode and use Ctrl+Alt+Space to toggle listening.",
    )
    parser.add_argument(
        "--command-words",
        action="store_true",
        help="Enable command words like 換行, 逗號, 句號, 刪除.",
    )
    parser.add_argument(
        "--no-command-words",
        action="store_true",
        help="Keep command words disabled. This is the default.",
    )
    parser.add_argument(
        "--append-space",
        action="store_true",
        help="Append a space after pasted final text when it has no ending punctuation.",
    )
    parser.add_argument(
        "--final-dedupe-seconds",
        type=float,
        default=DEFAULT_FINAL_DEDUPE_SECONDS,
        help=(
            "Skip pasting the same final transcript again inside this many "
            f"seconds. Default: {DEFAULT_FINAL_DEDUPE_SECONDS}"
        ),
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=float,
        default=DEFAULT_IDLE_TIMEOUT_SECONDS,
        help=(
            "Stop the current listening session after this many seconds with "
            f"no recognized text. Use 0 to disable. Default: {DEFAULT_IDLE_TIMEOUT_SECONDS}"
        ),
    )
    args = parser.parse_args()

    if args.list_devices:
        # Listing devices must not create a Google client or touch Windows
        # clipboard state. It is a read-only diagnostic path for microphone setup.
        print(sd.query_devices())
        return 0

    audio_settings = AudioSettings(
        rate=args.rate,
        chunk_ms=args.chunk_ms,
        device=args.device,
    )
    dictation_settings = DictationSettings(
        paste_final=args.paste_final,
        # Both flags are accepted for CLI clarity, but command words stay off
        # unless explicitly enabled. --no-command-words wins if both are passed.
        command_words=args.command_words and not args.no_command_words,
        append_space=args.append_space,
        final_dedupe_seconds=args.final_dedupe_seconds,
        idle_timeout_seconds=args.idle_timeout_seconds,
    )

    try:
        if args.hotkey:
            # Hotkey mode starts idle and creates a listening session only after
            # Ctrl+Alt+Space. This avoids recording or billing while the user is
            # not actively dictating.
            HotkeyDictationApp(
                args.language,
                audio_settings,
                dictation_settings,
            ).run()
        else:
            # Non-hotkey mode preserves the earlier prototype behavior: start a
            # listening session immediately, useful for quick console tests.
            listen(args.language, audio_settings, dictation_settings)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
