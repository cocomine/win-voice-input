from app_config import AudioSettings, DictationSettings
from dictation_controller import DictationController
from global_hotkey import GlobalHotkeyListener


class HotkeyDictationApp:
    # The console hotkey app owns no dictation internals. It wires a global
    # hotkey listener to DictationController, keeping CLI hotkey behavior and
    # tray hotkey behavior on the same lifecycle code.
    def __init__(
        self,
        language: str,
        audio_settings: AudioSettings,
        dictation_settings: DictationSettings,
    ):
        self.controller = DictationController(
            language,
            audio_settings,
            dictation_settings,
            self._on_status_change,
        )
        self.hotkey_listener = GlobalHotkeyListener()

    def run(self) -> None:
        print("Status: Idle. Press Ctrl+Alt+Space to start or pause.")
        print("Press Ctrl+C in this terminal to exit.\n")

        try:
            self.hotkey_listener.run(self.controller.toggle)
        finally:
            self.controller.shutdown()

    def _on_status_change(self, status: str) -> None:
        if status == "Listening":
            print("Status: Listening. Press Ctrl+Alt+Space to pause.")
        elif status == "Stopping":
            print("Status: Stopping current dictation session...")
        elif status == "Idle":
            print("Status: Idle. Press Ctrl+Alt+Space to start.")
