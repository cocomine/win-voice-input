import contextlib
import io
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import AudioSettings, DictationSettings  # noqa: E402
import dictation.dictation_session as session  # noqa: E402


def make_result(transcript: str, *, is_final: bool) -> SimpleNamespace:
    return SimpleNamespace(
        alternatives=[SimpleNamespace(transcript=transcript)],
        is_final=is_final,
        stability=0.7,
    )


class FakeMicrophoneStream:
    def __init__(
        self,
        settings: AudioSettings,
        stop_event: threading.Event | None = None,
    ):
        self.settings = settings
        self.stop_event = stop_event

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def generator(self):
        return iter(())


class DictationSessionStopTests(unittest.TestCase):
    def run_session_with_responses(
        self,
        responses: list[SimpleNamespace],
        *,
        paste_final: bool,
        paste_preview_on_session_end: bool,
    ) -> tuple[list[str], list[str]]:
        pasted_text: list[str] = []
        overlay_text: list[str] = []

        class FakeSpeechClient:
            def streaming_recognize(self, streaming_config, requests):
                return iter(responses)

        class FakeWindowsTextOutput:
            def paste_text(self, text: str) -> None:
                pasted_text.append(text)

            def press_backspace(self) -> None:
                pasted_text.append("<backspace>")

        stop_event = threading.Event()
        # These tests model the race that happens when Ctrl+Alt+Space stops the
        # session while Google has already delivered one more response. The
        # response must still be processed before listen() leaves the stream.
        stop_event.set()

        with (
            patch.object(session.speech, "SpeechClient", FakeSpeechClient),
            patch.object(session, "MicrophoneStream", FakeMicrophoneStream),
            patch.object(session, "WindowsTextOutput", FakeWindowsTextOutput),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            session.listen(
                "yue-Hant-HK",
                AudioSettings(rate=16000, chunk_ms=100, device=None),
                DictationSettings(
                    paste_final=paste_final,
                    paste_preview_on_session_end=paste_preview_on_session_end,
                    command_words=False,
                    append_space=False,
                    final_dedupe_seconds=0.8,
                    idle_timeout_seconds=0,
                ),
                stop_event,
                overlay_text.append,
            )

        return pasted_text, overlay_text

    def test_stop_event_keeps_latest_delivered_interim_for_preview_paste(self):
        response = SimpleNamespace(
            results=[
                make_result("older preview", is_final=False),
                make_result("latest preview", is_final=False),
            ]
        )

        pasted_text, overlay_text = self.run_session_with_responses(
            [response],
            paste_final=True,
            paste_preview_on_session_end=True,
        )

        self.assertEqual(pasted_text, ["latest preview"])
        self.assertEqual(overlay_text, ["latest preview", ""])

    def test_final_in_stopped_response_clears_pending_preview(self):
        response = SimpleNamespace(
            results=[
                make_result("draft preview", is_final=False),
                make_result("committed final", is_final=True),
            ]
        )

        pasted_text, overlay_text = self.run_session_with_responses(
            [response],
            paste_final=True,
            paste_preview_on_session_end=True,
        )

        self.assertEqual(pasted_text, ["committed final"])
        self.assertEqual(overlay_text, ["", ""])

    def test_disabled_session_end_preview_does_not_paste_stopped_interim(self):
        response = SimpleNamespace(
            results=[make_result("preview should stay visible only", is_final=False)]
        )

        pasted_text, overlay_text = self.run_session_with_responses(
            [response],
            paste_final=True,
            paste_preview_on_session_end=False,
        )

        self.assertEqual(pasted_text, [])
        self.assertEqual(overlay_text, ["preview should stay visible only", ""])


if __name__ == "__main__":
    unittest.main()
