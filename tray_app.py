import sys
import threading

import pystray
from PIL import Image, ImageDraw

from app_config import AudioSettings, DictationSettings
from dictation_controller import DictationController
from global_hotkey import GlobalHotkeyListener


class TrayDictationApp:
    # The tray app is a UI shell around DictationController. It does not own
    # microphone, Google STT, or paste logic; it only exposes Start/Pause/Exit
    # controls and mirrors controller status into the icon and menu.
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
        self.icon: pystray.Icon | None = None
        self._hotkey_thread: threading.Thread | None = None
        self._images = {
            "Idle": self._create_status_image((115, 115, 115)),
            "Listening": self._create_status_image((20, 170, 85)),
            "Stopping": self._create_status_image((230, 160, 35)),
        }

    def run(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: f"Status: {self.controller.status}",
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                "Start listening",
                self._on_start,
                enabled=lambda item: self.controller.status == "Idle",
            ),
            pystray.MenuItem(
                "Pause listening",
                self._on_pause,
                enabled=lambda item: self.controller.status == "Listening",
            ),
            pystray.MenuItem("Exit", self._on_exit),
        )
        self.icon = pystray.Icon(
            "win-voice-input",
            self._images["Idle"],
            "Win Voice Input - Idle",
            menu,
        )
        print(
            "Tray icon started. Press Ctrl+Alt+Space or use the tray menu "
            "to start or pause."
        )
        self.icon.run(setup=self._on_setup)

    def _on_setup(self, icon: pystray.Icon) -> None:
        # pystray only auto-shows the icon when no custom setup callback is
        # provided. Because V4 uses setup to start the hotkey thread, we must
        # explicitly mark the tray icon visible here.
        icon.visible = True

        # pystray owns the main UI loop after icon.run(). The hotkey listener
        # therefore runs in one background thread so Ctrl+Alt+Space remains
        # available while the tray menu is open or idle.
        self._hotkey_thread = threading.Thread(
            target=self._run_hotkey_listener,
            daemon=True,
        )
        self._hotkey_thread.start()

    def _run_hotkey_listener(self) -> None:
        try:
            self.hotkey_listener.run(self.controller.toggle)
        except Exception as exc:
            print(f"\nHotkey listener error: {exc}", file=sys.stderr, flush=True)
            if self.icon is not None:
                self.icon.stop()

    def _on_start(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.controller.start()

    def _on_pause(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.controller.stop()

    def _on_exit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.controller.shutdown()
        self.hotkey_listener.stop()
        icon.stop()

    def _on_status_change(self, status: str) -> None:
        print(f"Status: {status}")
        if self.icon is None:
            return

        # Status is visible in both the icon color and hover title. The menu is
        # updated because Start/Pause enabled states depend on controller.status.
        self.icon.icon = self._images[status]
        self.icon.title = f"Win Voice Input - {status}"
        self.icon.update_menu()

    def _create_status_image(self, color: tuple[int, int, int]) -> Image.Image:
        # The icon is generated at runtime to avoid adding binary assets. A
        # simple colored circle is enough for state recognition in the tray:
        # gray=Idle, green=Listening, amber=Stopping.
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((8, 8, 56, 56), fill=color, outline=(30, 30, 30), width=4)
        draw.rectangle((28, 18, 36, 46), fill=(255, 255, 255, 230))
        draw.rectangle((20, 38, 44, 46), fill=(255, 255, 255, 230))
        return image
