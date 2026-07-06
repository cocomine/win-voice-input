import logging
import os
import sys
import threading
import winreg
from io import BytesIO
from pathlib import Path

import pystray
from PIL import Image
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

from config import (
    AudioSettings,
    CONFIG_SAVED_RESTART_EXIT_CODE,
    DictationSettings,
    FeedbackSettings,
    get_asset_dir,
)
from dictation.dictation_controller import DictationController
from ui.error_dialog import show_error_message
from ui.global_hotkey_listener import (
    GlobalHotkeyListener,
    HOTKEY_DISPLAY_NAME,
    PREVIEW_COMMIT_KEY_DISPLAY_NAME,
)
from ui.listening_indicator import ListeningIndicator

logger = logging.getLogger(__name__)


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
    SOURCE_ENTRY_POINT_FILE_NAME = "voice_input.py"
    STARTUP_NOTIFICATION_TITLE = "Win Voice Input"

    def __init__(
        self,
        language: str,
        audio_settings: AudioSettings,
        dictation_settings: DictationSettings,
        feedback_settings: FeedbackSettings,
        config_path: Path,
        log_dir: Path,
        hotkey_enabled: bool = True,
    ):
        self.config_path = config_path
        self.log_dir = log_dir
        self.hotkey_enabled = hotkey_enabled
        self.controller = DictationController(
            language,
            audio_settings,
            dictation_settings,
            feedback_settings,
            self._on_status_change,
            self._on_recognition_text,
        )
        self.hotkey_listener = GlobalHotkeyListener()
        self.listening_indicator = (
            ListeningIndicator(feedback_settings.listening_indicator_position)
            if feedback_settings.show_listening_indicator
            else None
        )
        self.icon: pystray.Icon | None = None
        self._hotkey_thread: threading.Thread | None = None
        self._restart_command: list[str] | None = None
        self._restart_lock = threading.Lock()
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
            pystray.MenuItem("Settings...", self._on_open_settings),
            pystray.MenuItem("Open logs folder", self._on_open_logs_folder),
            pystray.MenuItem("Open config folder", self._on_open_config_folder),
            pystray.MenuItem("Exit", self._on_exit),
        )
        self.icon = pystray.Icon(
            "win-voice-input",
            self._images["Idle"],
            "Win Voice Input - Idle",
            menu,
        )
        if self.hotkey_enabled:
            print(
                f"Tray icon started. Press {HOTKEY_DISPLAY_NAME} or use the "
                f"tray menu to start or pause. While listening, press "
                f"{PREVIEW_COMMIT_KEY_DISPLAY_NAME} to paste preview."
            )
        else:
            print("Tray icon started. Use the tray menu to start or pause.")
        logger.info("Tray icon started.")
        self.icon.run(setup=self._on_setup)
        with self._restart_lock:
            restart_command = self._restart_command
        if restart_command is not None:
            # The watcher thread only asks pystray to stop. Runtime cleanup is
            # done here on the tray main thread so controller, hotkey, and
            # indicator state are not mutated from two threads at once.
            self._shutdown_runtime()
            # subprocess is imported only for the restart path so normal tray
            # startup keeps module-level imports focused on always-used pieces.
            import subprocess

            try:
                subprocess.Popen(restart_command)
                logger.info("Restarted Win Voice Input after settings save.")
            except OSError as exc:
                logger.exception("Failed to restart after settings save.")
                show_error_message(
                    "Win Voice Input Restart Error",
                    f"Settings were saved, but the app could not restart:\n\n{exc}",
                )
                print(
                    f"\nFailed to restart Win Voice Input: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

    def _on_setup(self, icon: pystray.Icon) -> None:
        # pystray only auto-shows the icon when no custom setup callback is
        # provided. Because V4 uses setup to start the hotkey thread, we must
        # explicitly mark the tray icon visible here.
        icon.visible = True

        if self.hotkey_enabled:
            # pystray owns the main UI loop after icon.run(). The hotkey listener
            # therefore runs in one background thread so the configured shortcut
            # remains available while the tray menu is open or idle.
            self._hotkey_thread = threading.Thread(
                target=self._run_hotkey_listener,
                daemon=True,
            )
            self._hotkey_thread.start()

        # Windowed builds intentionally stay in the background after startup.
        # A tray notification confirms that the app is ready and tells the user
        # which control starts dictation, without blocking the tray UI loop.
        try:
            if self.hotkey_enabled:
                message = (
                    f"App is running. Press {HOTKEY_DISPLAY_NAME} or use the "
                    "tray menu to start voice input. While listening, press "
                    f"{PREVIEW_COMMIT_KEY_DISPLAY_NAME} to paste preview."
                )
            else:
                message = "App is running. Use the tray menu to start voice input."
            icon.notify(message, self.STARTUP_NOTIFICATION_TITLE)
            logger.info("Startup notification shown.")
        except Exception as exc:
            # The startup notification is helpful but not required for
            # microphone, hotkey, or tray operation. If Windows refuses the
            # notification, keep the app running and leave diagnostics in logs.
            logger.warning(
                "Failed to show startup notification; continuing without it: %s",
                exc,
                exc_info=True,
            )

    def _run_hotkey_listener(self) -> None:
        try:
            self.hotkey_listener.run(
                self.controller.toggle,
                self.controller.request_preview_commit,
            )
        except Exception as exc:
            logger.exception("Hotkey listener failed.")
            show_error_message(
                "Win Voice Input Hotkey Error",
                f"Hotkey listener failed:\n\n{exc}",
            )
            print(f"\nHotkey listener error: {exc}", file=sys.stderr, flush=True)
            if self.icon is not None:
                self.icon.stop()

    def _on_start(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.controller.start()

    def _on_pause(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.controller.stop()

    def _build_app_command(self, settings_mode: bool) -> list[str]:
        # Source and packaged runs need different entry points. Keeping command
        # construction in one place ensures Settings and restart launch the same
        # application path and the same config.json file.
        if getattr(sys, "frozen", False):
            command = [
                sys.executable,
            ]
        else:
            # tray_dictation_app.py now lives under src/ui; the runnable source
            # entry remains at src/voice_input.py so scripts and PyInstaller
            # keep one stable entry point.
            source_entry_point = (
                Path(__file__).resolve().parent.parent
                / self.SOURCE_ENTRY_POINT_FILE_NAME
            )
            command = [
                sys.executable,
                str(source_entry_point),
            ]
        if settings_mode:
            command.append("--settings")
        command.extend(["--config", str(self.config_path)])
        return command

    def _on_open_settings(
        self,
        icon: pystray.Icon,
        item: pystray.MenuItem,
    ) -> None:
        import subprocess

        # The settings editor runs in a separate process because Qt owns its own
        # event loop. Keeping it out of the pystray process avoids UI-loop
        # contention while dictation and tray hotkeys keep running.
        command = self._build_app_command(settings_mode=True)
        try:
            settings_process = subprocess.Popen(command)
            logger.info("Opened settings editor for config: %s", self.config_path)
        except OSError as exc:
            logger.exception("Failed to open settings editor.")
            show_error_message(
                "Win Voice Input Settings Error",
                f"Unable to open settings editor:\n\n{exc}",
            )
            print(
                f"\nFailed to open settings editor: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return

        def wait_for_settings_close() -> None:
            exit_code = settings_process.wait()
            if exit_code != CONFIG_SAVED_RESTART_EXIT_CODE:
                return

            logger.info("Settings save requested app restart.")
            with self._restart_lock:
                self._restart_command = self._build_app_command(settings_mode=False)
            if self.icon is not None:
                self.icon.stop()

        # Waiting in a background thread keeps the tray menu responsive while
        # the user edits settings. The child process exit code is the explicit
        # signal that config.json was saved; this thread records the restart
        # request and stops pystray, while main-thread cleanup happens after
        # icon.run() returns.
        threading.Thread(
            target=wait_for_settings_close,
            daemon=True,
        ).start()

    def _on_open_logs_folder(
        self,
        icon: pystray.Icon,
        item: pystray.MenuItem,
    ) -> None:
        self._open_folder(self.log_dir, "logs")

    def _on_open_config_folder(
        self,
        icon: pystray.Icon,
        item: pystray.MenuItem,
    ) -> None:
        self._open_folder(self.config_path.parent, "config")

    def _on_exit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._shutdown_runtime()
        icon.stop()

    def _shutdown_runtime(self) -> None:
        # Shutdown order matters: dictation stops first so audio/STT work ends,
        # then the visual indicator and hotkey listener release their Win32
        # resources before either normal exit or restart.
        self.controller.shutdown()
        if self.listening_indicator is not None:
            self.listening_indicator.shutdown()
        self.hotkey_listener.stop()

    def _on_status_change(self, status: str) -> None:
        print(f"Status: {status}")
        logger.info("Status changed: %s", status)
        if self.listening_indicator is not None:
            if status == "Listening":
                self.listening_indicator.set_text("")
                self.listening_indicator.show()
            else:
                self.listening_indicator.set_text("")
                self.listening_indicator.hide()

        if self.icon is None:
            return

        # Status is visible in both the icon color and hover title. The menu is
        # updated because Start/Pause enabled states depend on controller.status.
        self.icon.icon = self._images[status]
        self.icon.title = f"Win Voice Input - {status}"
        self.icon.update_menu()

    def _on_recognition_text(self, text: str) -> None:
        # Recognition text is shown only on the overlay. It deliberately stays
        # out of the active editor until Google returns a final result.
        if self.listening_indicator is not None:
            self.listening_indicator.set_text(text)

    def _open_folder(self, folder_path: Path, folder_label: str) -> None:
        # Tray callbacks run on the pystray UI thread. Opening a folder should
        # never touch dictation state; errors are logged and shown in the
        # console so windowed builds keep a diagnostic trail without crashing.
        if not folder_path.is_dir():
            message = f"{folder_label.title()} folder does not exist: {folder_path}"
            logger.error(message)
            show_error_message("Win Voice Input Error", message)
            print(f"\n{message}", file=sys.stderr, flush=True)
            return
        try:
            os.startfile(str(folder_path))
            logger.info("Opened %s folder: %s", folder_label, folder_path)
        except OSError as exc:
            logger.exception(
                "Failed to open %s folder: %s",
                folder_label,
                folder_path,
            )
            show_error_message(
                "Win Voice Input Error",
                f"Failed to open {folder_label} folder:\n\n{exc}",
            )
            print(
                f"\nFailed to open {folder_label} folder: {exc}",
                file=sys.stderr,
                flush=True,
            )

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
                "Source runs expect assets\\mic.svg and assets\\mic-mute.svg "
                "under the project root; packaged runs expect the same assets "
                "folder bundled under PyInstaller's internal data directory."
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
