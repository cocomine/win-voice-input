import sys
import threading
import winreg
from io import BytesIO
from pathlib import Path

import pystray
from PIL import Image
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

from app_config import (
    AudioSettings,
    DictationSettings,
    FeedbackSettings,
    get_asset_dir,
)
from dictation_controller import DictationController
from global_hotkey import GlobalHotkeyListener, HOTKEY_DISPLAY_NAME
from listening_indicator import ListeningIndicator


class TrayDictationApp:
    # The tray app is a UI shell around DictationController. It does not own
    # microphone, Google STT, or paste logic; it only exposes Start/Pause/Exit
    # controls and mirrors controller status into the icon and menu.
    ICON_SIZE = 64
    # These colors are part of the user-visible tray status language. White is
    # kept for dark Windows system UI, while dark gray is used on light system
    # UI so the muted icon stays visible against a light taskbar.
    LISTENING_ICON_COLOR = "#20AA55"
    MUTED_ICON_COLOR_ON_DARK_SYSTEM_UI = "#FFFFFF"
    MUTED_ICON_COLOR_ON_LIGHT_SYSTEM_UI = "#303030"
    THEME_REGISTRY_PATH = (
        "Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize"
    )
    SYSTEM_LIGHT_THEME_VALUE = "SystemUsesLightTheme"

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
        self.icon: pystray.Icon | None = None
        self._hotkey_thread: threading.Thread | None = None
        # Tray artwork is loaded from the shared assets folder so source runs
        # and packaged runs use the same required SVG files.
        asset_dir = get_asset_dir()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.THEME_REGISTRY_PATH,
            ) as key:
                system_uses_light_theme = winreg.QueryValueEx(
                    key,
                    self.SYSTEM_LIGHT_THEME_VALUE,
                )[0]
        except FileNotFoundError:
            # Some Windows versions or fresh profiles can omit this value until
            # Personalization settings are changed. Because white can disappear
            # on light taskbars, an absent value uses the light-UI contrast
            # choice. This is a conservative visibility choice, not a claim that
            # every Windows version defaults to light system UI.
            system_uses_light_theme = 1
        except OSError as exc:
            raise RuntimeError(
                "Unable to read Windows system theme for tray icon color from "
                f"HKCU\\{self.THEME_REGISTRY_PATH}\\"
                f"{self.SYSTEM_LIGHT_THEME_VALUE}: {exc}"
            ) from exc

        if system_uses_light_theme == 1:
            self._muted_icon_color = self.MUTED_ICON_COLOR_ON_LIGHT_SYSTEM_UI
        elif system_uses_light_theme == 0:
            self._muted_icon_color = self.MUTED_ICON_COLOR_ON_DARK_SYSTEM_UI
        else:
            raise RuntimeError(
                "Unexpected Windows system theme value from "
                f"HKCU\\{self.THEME_REGISTRY_PATH}\\"
                f"{self.SYSTEM_LIGHT_THEME_VALUE}: expected 0 or 1, "
                f"got {system_uses_light_theme!r}."
            )
        muted_icon = self._render_svg_icon(
            asset_dir / "mic-mute.svg",
            self._muted_icon_color,
        )
        self._images = {
            "Idle": muted_icon,
            "Listening": self._render_svg_icon(
                asset_dir / "mic.svg",
                self.LISTENING_ICON_COLOR,
            ),
            "Stopping": muted_icon,
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
            f"Tray icon started. Press {HOTKEY_DISPLAY_NAME} or use the "
            "tray menu to start or pause."
        )
        self.icon.run(setup=self._on_setup)

    def _on_setup(self, icon: pystray.Icon) -> None:
        # pystray only auto-shows the icon when no custom setup callback is
        # provided. Because V4 uses setup to start the hotkey thread, we must
        # explicitly mark the tray icon visible here.
        icon.visible = True

        # pystray owns the main UI loop after icon.run(). The hotkey listener
        # therefore runs in one background thread so the configured shortcut
        # remains available while the tray menu is open or idle.
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
        if self.listening_indicator is not None:
            self.listening_indicator.shutdown()
        self.hotkey_listener.stop()
        icon.stop()

    def _on_status_change(self, status: str) -> None:
        print(f"Status: {status}")
        if self.listening_indicator is not None:
            if status == "Listening":
                self.listening_indicator.show()
            else:
                self.listening_indicator.hide()

        if self.icon is None:
            return

        # Status is visible in both the icon color and hover title. The menu is
        # updated because Start/Pause enabled states depend on controller.status.
        self.icon.icon = self._images[status]
        self.icon.title = f"Win Voice Input - {status}"
        self.icon.update_menu()

    def _render_svg_icon(self, svg_path: Path, color: str) -> Image.Image:
        # The user-provided SVG files are the source of truth for tray artwork.
        # They use currentColor, so recoloring is done by replacing that token
        # before rendering. Missing or unsupported SVG files should raise clear
        # errors instead of silently switching to a different icon.
        try:
            svg_text = svg_path.read_text(encoding="utf-8").replace(
                "currentColor",
                color,
            )
        except OSError as exc:
            # The SVG files are required assets, not optional decoration. If
            # they are missing or unreadable, fail with a message that tells the
            # user exactly which project files must be checked.
            raise RuntimeError(
                f"Required tray icon SVG is missing or inaccessible: {svg_path}. "
                "Ensure mic.svg and mic-mute.svg are in the assets folder."
            ) from exc
        drawing = svg2rlg(BytesIO(svg_text.encode("utf-8")))
        if drawing is None:
            raise ValueError(f"Unable to render SVG icon: {svg_path}")

        # The source icons are 16x16. Scaling the ReportLab drawing before
        # rasterizing keeps the tray image crisp at the size expected by
        # pystray on high-DPI Windows displays.
        drawing.scale(
            self.ICON_SIZE / drawing.width,
            self.ICON_SIZE / drawing.height,
        )
        drawing.width = self.ICON_SIZE
        drawing.height = self.ICON_SIZE

        png_bytes = renderPM.drawToString(
            drawing,
            fmt="PNG",
            bg=None,
            backendFmt="RGBA",
        )
        return Image.open(BytesIO(png_bytes)).convert("RGBA")
