import logging

from app_config import AudioSettings, DictationSettings, FeedbackSettings
from dictation_controller import DictationController
from global_hotkey import GlobalHotkeyListener, HOTKEY_DISPLAY_NAME
from listening_indicator import ListeningIndicator

logger = logging.getLogger(__name__)


class HotkeyDictationApp:
    # The console hotkey app owns no dictation internals. It wires a global
    # hotkey listener to DictationController, keeping CLI hotkey behavior and
    # tray hotkey behavior on the same lifecycle code.
    def __init__(
        self,
        language: str,
        audio_settings: AudioSettings,
        dictation_settings: DictationSettings,
        feedback_settings: FeedbackSettings,
    ):
        self.controller = DictationController(
            language,
            audio_settings,
            dictation_settings,
            feedback_settings,
            self._on_status_change,
        )
        self.hotkey_listener = GlobalHotkeyListener()
        self.listening_indicator = (
            ListeningIndicator(feedback_settings.listening_indicator_position)
            if feedback_settings.show_listening_indicator
            else None
        )

    def run(self) -> None:
        logger.info("Console hotkey app started.")
        print(f"Status: Idle. Press {HOTKEY_DISPLAY_NAME} to start or pause.")
        print("Press Ctrl+C in this terminal to exit.\n")

        try:
            self.hotkey_listener.run(self.controller.toggle)
        finally:
            self.controller.shutdown()
            if self.listening_indicator is not None:
                self.listening_indicator.shutdown()

    def _on_status_change(self, status: str) -> None:
        logger.info("Hotkey app status changed: %s", status)
        if status == "Listening":
            if self.listening_indicator is not None:
                self.listening_indicator.show()
            print(f"Status: Listening. Press {HOTKEY_DISPLAY_NAME} to pause.")
        elif status == "Stopping":
            if self.listening_indicator is not None:
                self.listening_indicator.hide()
            print("Status: Stopping current dictation session...")
        elif status == "Idle":
            if self.listening_indicator is not None:
                self.listening_indicator.hide()
            print(f"Status: Idle. Press {HOTKEY_DISPLAY_NAME} to start.")
