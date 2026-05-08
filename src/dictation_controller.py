import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from app_config import (
    AudioSettings,
    DictationSettings,
    FeedbackSettings,
    get_asset_dir,
)
from dictation_session import listen
from error_dialog import show_error_message

if TYPE_CHECKING:
    import pygame

# pygame reads this environment variable during import. Setting it at module
# load time keeps constructor calls free of hidden global-state changes and
# prevents the pygame greeting from appearing in normal CLI/build output.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

StatusCallback = Callable[[str], None]
RecognitionTextCallback = Callable[[str], None]
logger = logging.getLogger(__name__)


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
        feedback_settings: FeedbackSettings,
        on_status_change: StatusCallback | None = None,
        on_recognition_text: RecognitionTextCallback | None = None,
    ):
        self.language = language
        self.audio_settings = audio_settings
        self.dictation_settings = dictation_settings
        self.feedback_settings = feedback_settings
        self.on_status_change = on_status_change
        self.on_recognition_text = on_recognition_text
        self.status = "Idle"
        self._sound_error: type[Exception] | None = None
        self._start_sound: "pygame.mixer.Sound | None" = None
        self._end_sound: "pygame.mixer.Sound | None" = None
        if self.feedback_settings.play_status_sounds:
            asset_dir = get_asset_dir()
            # Status sounds are required only when enabled. They are resolved
            # during controller setup so a missing packaged asset fails before
            # the user starts dictation and wonders why no state sound is played.
            self._start_sound_path = self._resolve_required_asset(
                asset_dir,
                "start.mp3",
            )
            self._end_sound_path = self._resolve_required_asset(
                asset_dir,
                "end.mp3",
            )
            # pygame is imported only when status sounds are enabled. Diagnostic
            # CLI paths such as --help and --list-devices therefore stay
            # lightweight, and users who disable sounds do not need audio mixer
            # setup for the controller to start.
            try:
                import pygame
            except ImportError as exc:
                raise RuntimeError(
                    "pygame is required for start/end status sounds. "
                    "Install runtime dependencies from requirements.txt or set "
                    "playStatusSounds to false in config.json."
                ) from exc

            self._mixer = pygame.mixer
            self._sound_error = pygame.error
            try:
                if self._mixer.get_init() is None:
                    self._mixer.init()
                self._start_sound = self._mixer.Sound(str(self._start_sound_path))
                self._end_sound = self._mixer.Sound(str(self._end_sound_path))
            except pygame.error as exc:
                raise RuntimeError(
                    "Failed to initialize or load start.mp3/end.mp3 status "
                    "sounds. Check the sound files or set playStatusSounds to "
                    "false in config.json."
                ) from exc

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
                self.on_recognition_text,
            )
        except Exception as exc:
            logger.exception("Dictation session failed.")
            show_error_message(
                "Win Voice Input Dictation Error",
                f"Dictation session failed:\n\n{exc}",
            )
            print(f"\nDictation session error: {exc}", file=sys.stderr, flush=True)
        finally:
            # Returning to Idle here covers manual pause, tray pause, and
            # auto-stop from idle timeout, because all paths close the same
            # listening session.
            self._set_status("Idle")

    def _set_status(self, status: str) -> None:
        previous_status = self.status
        self.status = status
        logger.info("Dictation status changed: %s", status)
        if self.on_status_change is not None:
            self.on_status_change(status)

        # The cues are tied to actual lifecycle transitions, not button clicks.
        # That makes hotkey starts, tray starts, manual pauses, and idle-timeout
        # auto-stops all use the same audible status language.
        if not self.feedback_settings.play_status_sounds:
            return

        if status == "Listening":
            self._play_status_sound(self._start_sound)
        elif previous_status == "Listening" and status != "Listening":
            self._play_status_sound(self._end_sound)

    def _resolve_required_asset(self, asset_dir: Path, file_name: str) -> Path:
        asset_path = asset_dir / file_name
        if not asset_path.is_file():
            raise RuntimeError(
                f"Required status sound is missing: {asset_path}. "
                "Source runs expect assets\\start.mp3 and assets\\end.mp3 "
                "under the project root; packaged runs expect the same assets "
                "folder bundled under PyInstaller's internal data directory."
            )
        return asset_path

    def _play_status_sound(self, sound: "pygame.mixer.Sound | None") -> None:
        # Sound.play() is asynchronous, so the UI and microphone control do not
        # wait for the cue to finish. Playback is a user cue, not the dictation
        # state machine itself, so a temporary audio-channel issue is reported
        # without stopping microphone control or Google STT.
        if sound is None or self._sound_error is None:
            return

        try:
            channel = sound.play()
        except self._sound_error as exc:
            logger.warning("Status sound playback failed: %s", exc)
            print(f"\nStatus sound warning: {exc}", file=sys.stderr, flush=True)
            return

        if channel is None:
            logger.warning("Status sound playback skipped: no audio channel.")
            print(
                "\nStatus sound warning: no audio channel available.",
                file=sys.stderr,
                flush=True,
            )
