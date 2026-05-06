import sys
import threading
from collections.abc import Callable

from app_config import AudioSettings, DictationSettings
from dictation_session import listen


StatusCallback = Callable[[str], None]


class DictationController:
    # This controller owns the start/stop lifecycle shared by console hotkey and
    # tray UI. Keeping lifecycle in one place prevents the two frontends from
    # drifting into different behavior when stopping microphone or Google STT
    # sessions.
    def __init__(
        self,
        language: str,
        audio_settings: AudioSettings,
        dictation_settings: DictationSettings,
        on_status_change: StatusCallback | None = None,
    ):
        self.language = language
        self.audio_settings = audio_settings
        self.dictation_settings = dictation_settings
        self.on_status_change = on_status_change
        self.status = "Idle"
        self._lock = threading.Lock()
        self._stop_event: threading.Event | None = None
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return

            # Each listening period gets a fresh event. Reusing an already-set
            # event would make the microphone generator exit immediately on the
            # next start.
            self._stop_event = threading.Event()
            # The worker thread is the only place that opens the microphone and
            # the Google stream. UI loops must remain responsive while
            # recognition is running.
            self._worker = threading.Thread(
                target=self._run_listening_session,
                daemon=True,
            )
            self._set_status("Listening")
            self._worker.start()

    def stop(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                return

            # Stopping is done by signalling the microphone generator to finish.
            # That prevents new audio chunks from being sent to Google after the
            # user pauses dictation.
            if self._stop_event is not None:
                self._stop_event.set()
            self._set_status("Stopping")

    def toggle(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self.stop()
        else:
            self.start()

    def shutdown(self) -> None:
        self.stop()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=5)

    def _run_listening_session(self) -> None:
        try:
            listen(
                self.language,
                self.audio_settings,
                self.dictation_settings,
                self._stop_event,
            )
        except Exception as exc:
            print(f"\nDictation session error: {exc}", file=sys.stderr, flush=True)
        finally:
            # Returning to Idle here covers manual pause, tray pause, and
            # auto-stop from idle timeout, because all paths close the same
            # listening session.
            self._set_status("Idle")

    def _set_status(self, status: str) -> None:
        self.status = status
        if self.on_status_change is not None:
            self.on_status_change(status)
