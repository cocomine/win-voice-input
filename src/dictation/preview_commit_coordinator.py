import logging
import sys
import threading
from collections.abc import Callable

from config import DictationSettings
from dictation.text_processing import prepare_text
from output.windows_text_output import WindowsTextOutput

logger = logging.getLogger(__name__)
RecognitionTextCallback = Callable[[str], None]


def normalize_preview_key(transcript: str) -> str:
    # Manual preview commits need to suppress the later Google final only when
    # it is the same recognized content. Normalizing whitespace matches the
    # existing final dedupe behavior without treating punctuation changes as
    # identical text.
    return " ".join(transcript.split())


class PreviewCommitCoordinator:
    # The dictation worker owns Google responses, while the Enter hook runs on a
    # Win32 input thread. This coordinator is the small locked boundary between
    # them: listen() publishes the latest non-final transcript, and the hook can
    # ask for that transcript to be committed without stopping the session.
    def __init__(self):
        self._lock = threading.Lock()
        self._pending_preview_text = ""
        self._committed_preview_keys: list[str] = []
        self._text_output: WindowsTextOutput | None = None
        self._dictation_settings: DictationSettings | None = None
        self._on_recognition_text: RecognitionTextCallback | None = None

    def attach(
        self,
        text_output: WindowsTextOutput | None,
        dictation_settings: DictationSettings,
        on_recognition_text: RecognitionTextCallback | None,
    ) -> None:
        with self._lock:
            self._text_output = text_output
            self._dictation_settings = dictation_settings
            self._on_recognition_text = on_recognition_text
            self._pending_preview_text = ""
            self._committed_preview_keys.clear()

    def detach(self) -> None:
        with self._lock:
            self._text_output = None
            self._dictation_settings = None
            self._on_recognition_text = None
            self._pending_preview_text = ""
            self._committed_preview_keys.clear()

    def set_pending_preview(self, transcript: str) -> None:
        with self._lock:
            # Google can keep repeating the same interim text after the user has
            # pressed Enter to commit it. Ignoring exact committed matches keeps
            # a later session-end cleanup from pasting the same preview again.
            if normalize_preview_key(transcript) in self._committed_preview_keys:
                self._pending_preview_text = ""
                return
            self._pending_preview_text = transcript

    def clear_pending_preview(self) -> None:
        with self._lock:
            self._pending_preview_text = ""

    def has_pending_preview(self) -> bool:
        with self._lock:
            return bool(self._pending_preview_text)

    def consume_committed_preview(self, transcript: str) -> bool:
        key = normalize_preview_key(transcript)
        if not key:
            return False

        with self._lock:
            if key not in self._committed_preview_keys:
                return False
            self._committed_preview_keys.remove(key)
            return True

    def commit_pending_preview(
        self,
        source_name: str,
        *,
        clear_overlay: bool = True,
    ) -> str:
        with self._lock:
            transcript = self._pending_preview_text
            output = self._text_output
            settings = self._dictation_settings
            on_recognition_text = self._on_recognition_text
            if not transcript or output is None or settings is None:
                return ""

            # Clear before output so repeated Enter keydown events cannot paste
            # the same preview twice while the clipboard operation is running.
            self._pending_preview_text = ""
            preview_key = normalize_preview_key(transcript)
            if preview_key:
                self._committed_preview_keys.append(preview_key)

            text, action = prepare_text(transcript, settings)
            try:
                if action == "backspace":
                    output.press_backspace()
                elif text:
                    output.paste_text(text)
                else:
                    if preview_key in self._committed_preview_keys:
                        self._committed_preview_keys.remove(preview_key)
                    return ""
            except OSError as exc:
                if preview_key in self._committed_preview_keys:
                    self._committed_preview_keys.remove(preview_key)
                logger.warning("%s output failed: %s", source_name, exc)
                print(
                    f"\n{source_name} warning: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                return ""

        if clear_overlay and on_recognition_text is not None:
            on_recognition_text("")
        logger.info("%s committed pending preview text.", source_name)
        return transcript
