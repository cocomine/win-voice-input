import json
import logging
import stat
import sys
import subprocess
import winreg
from pathlib import Path

import sounddevice as sd

from app_config import (
    ALLOWED_LISTENING_INDICATOR_POSITIONS,
    DEFAULT_FINAL_DEDUPE_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_LISTENING_INDICATOR_POSITION,
    DEFAULT_PLAY_STATUS_SOUNDS,
    DEFAULT_RATE,
    DEFAULT_SHOW_LISTENING_INDICATOR,
)

logger = logging.getLogger(__name__)
STARTUP_RUN_REGISTRY_PATH = "Software\\Microsoft\\Windows\\CurrentVersion\\Run"
STARTUP_RUN_VALUE_NAME = "WinVoiceInput"


class ConfigEditorWindow:
    # The settings editor is intentionally isolated from tray and dictation
    # code. It edits config.json only; current listening sessions keep their
    # already-loaded settings until the app is restarted.
    def __init__(self, config_path: Path):
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QPushButton,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )

        self.config_path = config_path
        self.startup_registry_available = True
        # Windows Run keys store a single command line string. For packaged
        # runs, sys.executable is WinVoiceInput.exe; for source runs, the
        # current Python executable must launch src\voice_input.py directly.
        # --config is included so startup uses the same settings file that this
        # editor is currently editing, even when it is not the default path.
        startup_command_parts = [str(Path(sys.executable).resolve())]
        if not getattr(sys, "frozen", False):
            startup_command_parts.append(
                str(Path(__file__).resolve().parent / "voice_input.py")
            )
        startup_command_parts.extend(["--config", str(self.config_path)])
        self.startup_command = subprocess.list2cmdline(startup_command_parts)
        self.config_data = self._read_config()
        self.window = QMainWindow()
        self.window.setWindowTitle("Win Voice Input Settings")
        self.window.resize(560, 520)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        root_layout.addLayout(form)

        self.credentials_edit = QLineEdit()
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._choose_credentials_file)
        credentials_row = QHBoxLayout()
        credentials_row.addWidget(self.credentials_edit)
        credentials_row.addWidget(browse_button)
        form.addRow("Google credentials JSON", credentials_row)

        self.device_combo = QComboBox()
        self.device_combo.addItem("Windows default input device", None)
        self._populate_devices()
        form.addRow("Input device", self.device_combo)

        self.language_edit = QLineEdit()
        form.addRow("Language", self.language_edit)

        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(8000, 192000)
        self.rate_spin.setSingleStep(1000)
        form.addRow("Sample rate", self.rate_spin)

        self.paste_final_check = QCheckBox("Paste final transcripts")
        form.addRow("", self.paste_final_check)

        self.append_space_check = QCheckBox("Append space after final text")
        form.addRow("", self.append_space_check)

        self.command_words_check = QCheckBox("Enable command words")
        form.addRow("", self.command_words_check)

        self.final_dedupe_spin = QDoubleSpinBox()
        self.final_dedupe_spin.setRange(0.0, 30.0)
        self.final_dedupe_spin.setDecimals(2)
        self.final_dedupe_spin.setSingleStep(0.1)
        form.addRow("Duplicate protection seconds", self.final_dedupe_spin)

        self.idle_timeout_spin = QDoubleSpinBox()
        self.idle_timeout_spin.setRange(0.0, 120.0)
        self.idle_timeout_spin.setDecimals(1)
        self.idle_timeout_spin.setSingleStep(1.0)
        form.addRow("Idle auto-stop seconds", self.idle_timeout_spin)

        self.play_sounds_check = QCheckBox("Play start/end sounds")
        form.addRow("", self.play_sounds_check)

        self.show_indicator_check = QCheckBox("Show listening indicator")
        form.addRow("", self.show_indicator_check)

        self.indicator_position_combo = QComboBox()
        for position in ALLOWED_LISTENING_INDICATOR_POSITIONS:
            self.indicator_position_combo.addItem(position, position)
        form.addRow("Indicator position", self.indicator_position_combo)

        self.start_with_windows_check = QCheckBox("Start with Windows")
        form.addRow("", self.start_with_windows_check)

        note = QLabel("Changes are saved to config.json and take effect after restarting the app.")
        note.setWordWrap(True)
        root_layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self._save_config)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.window.close)
        buttons.addWidget(save_button)
        buttons.addWidget(close_button)
        root_layout.addLayout(buttons)

        self.window.setCentralWidget(root)
        self._load_values_into_widgets()

    def show(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _read_config(self) -> dict:
        from PySide6.QtWidgets import QMessageBox

        if not self.config_path.exists():
            return {}
        try:
            config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.critical(
                None,
                "Unable to read config",
                f"Unable to read config file:\n{self.config_path}\n\n{exc}",
            )
            logger.exception("Config editor failed to read config: %s", self.config_path)
            return {}
        if not isinstance(config_data, dict):
            QMessageBox.critical(
                None,
                "Invalid config",
                f"Config file must contain a JSON object:\n{self.config_path}",
            )
            logger.error("Config editor found non-object config: %s", self.config_path)
            return {}
        return config_data

    def _populate_devices(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        try:
            devices = sd.query_devices()
        except Exception as exc:
            QMessageBox.critical(
                self.window,
                "Unable to list microphones",
                f"Unable to list input devices.\n\n{exc}",
            )
            logger.exception("Config editor failed to list audio devices.")
            return

        for index, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            self.device_combo.addItem(f"{device['name']} (index {index})", index)

    def _load_values_into_widgets(self) -> None:
        self.credentials_edit.setText(str(self.config_data.get("credentials", "")))

        configured_device = self.config_data.get("device")
        device_was_found = False
        for index in range(self.device_combo.count()):
            if self.device_combo.itemData(index) == configured_device:
                self.device_combo.setCurrentIndex(index)
                device_was_found = True
                break
        if configured_device is not None and not device_was_found:
            # A configured microphone can be unplugged while the setting is
            # still valid for the user's normal setup. Keep that explicit index
            # visible so opening and saving settings does not silently switch
            # back to the Windows default input device.
            self.device_combo.addItem(
                f"Configured device index {configured_device} (not available)",
                configured_device,
            )
            self.device_combo.setCurrentIndex(self.device_combo.count() - 1)

        self.language_edit.setText(str(self.config_data.get("language", DEFAULT_LANGUAGE)))
        self.rate_spin.setValue(int(self.config_data.get("rate", DEFAULT_RATE)))
        self.paste_final_check.setChecked(bool(self.config_data.get("pasteFinal", True)))
        self.append_space_check.setChecked(bool(self.config_data.get("appendSpace", False)))
        self.command_words_check.setChecked(bool(self.config_data.get("commandWords", False)))
        self.final_dedupe_spin.setValue(
            float(self.config_data.get("finalDedupeSeconds", DEFAULT_FINAL_DEDUPE_SECONDS))
        )
        self.idle_timeout_spin.setValue(
            float(self.config_data.get("idleTimeoutSeconds", DEFAULT_IDLE_TIMEOUT_SECONDS))
        )
        self.play_sounds_check.setChecked(
            bool(self.config_data.get("playStatusSounds", DEFAULT_PLAY_STATUS_SOUNDS))
        )
        self.show_indicator_check.setChecked(
            bool(
                self.config_data.get(
                    "showListeningIndicator",
                    DEFAULT_SHOW_LISTENING_INDICATOR,
                )
            )
        )
        configured_position = str(
            self.config_data.get(
                "listeningIndicatorPosition",
                DEFAULT_LISTENING_INDICATOR_POSITION,
            )
        ).strip().lower()
        index = self.indicator_position_combo.findData(configured_position)
        if index >= 0:
            self.indicator_position_combo.setCurrentIndex(index)

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_RUN_REGISTRY_PATH,
            ) as key:
                startup_command_value, startup_command_type = winreg.QueryValueEx(
                    key,
                    STARTUP_RUN_VALUE_NAME,
                )
        except FileNotFoundError:
            startup_command = ""
        except OSError as exc:
            # Reading the Run key is optional for dictation itself, but the
            # Settings window must not pretend it knows the startup state when
            # Windows refused access. The checkbox is disabled so Save cannot
            # accidentally overwrite an unknown registry state.
            from PySide6.QtWidgets import QMessageBox

            self.startup_registry_available = False
            self.start_with_windows_check.setEnabled(False)
            QMessageBox.critical(
                self.window,
                "Unable to read startup setting",
                "Unable to read Windows startup setting from:\n"
                f"HKCU\\{STARTUP_RUN_REGISTRY_PATH}\\{STARTUP_RUN_VALUE_NAME}"
                f"\n\n{type(exc).__name__}: {exc}",
            )
            logger.exception("Config editor failed to read Windows startup setting.")
            return
        else:
            if (
                startup_command_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ)
                or not isinstance(startup_command_value, str)
            ):
                # A Run value should be a command-line string. Disabling the
                # checkbox avoids converting an unexpected registry value into
                # a misleading checked/unchecked state.
                from PySide6.QtWidgets import QMessageBox

                self.startup_registry_available = False
                self.start_with_windows_check.setEnabled(False)
                QMessageBox.critical(
                    self.window,
                    "Invalid startup setting",
                    "Windows startup setting has an unexpected value type:\n"
                    f"HKCU\\{STARTUP_RUN_REGISTRY_PATH}\\{STARTUP_RUN_VALUE_NAME}"
                    f"\n\nExpected REG_SZ or REG_EXPAND_SZ string, got "
                    f"registry type {startup_command_type} and Python type "
                    f"{type(startup_command_value).__name__}.",
                )
                logger.error(
                    "Unexpected Windows startup registry value type: %r / %s",
                    startup_command_type,
                    type(startup_command_value).__name__,
                )
                return
            startup_command = startup_command_value

        self.start_with_windows_check.setChecked(bool(startup_command))

    def _choose_credentials_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        selected_path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Select Google credentials JSON",
            str(self.config_path.parent),
            "JSON files (*.json);;All files (*.*)",
        )
        if selected_path:
            if self._validate_credentials_path(selected_path) is None:
                return
            self.credentials_edit.setText(selected_path)

    def _validate_credentials_path(self, credentials_text: str) -> Path | None:
        from PySide6.QtWidgets import QMessageBox

        credentials_text = credentials_text.strip()
        if not credentials_text:
            # The windowed app cannot start dictation without Google
            # credentials. Blocking an empty value here gives first-time users a
            # clear fix before config.json is written.
            QMessageBox.critical(
                self.window,
                "Invalid Google credentials",
                "Google credentials JSON is required.",
            )
            return None

        credentials_path = Path(credentials_text)
        if not credentials_path.is_absolute():
            # Runtime resolves relative credential paths against config.json.
            # The editor validates the same resolved location so saving settings
            # and restarting the app use identical path rules.
            credentials_path = self.config_path.parent / credentials_path

        if credentials_path.suffix.lower() != ".json":
            QMessageBox.critical(
                self.window,
                "Invalid Google credentials",
                "Google credentials must be a .json file:\n"
                f"{credentials_path}",
            )
            return None

        try:
            credentials_stat = credentials_path.stat()
        except FileNotFoundError:
            QMessageBox.critical(
                self.window,
                "Invalid Google credentials",
                "Google credentials file does not exist:\n"
                f"{credentials_path}",
            )
            return None
        except OSError as exc:
            QMessageBox.critical(
                self.window,
                "Invalid Google credentials",
                "Unable to check Google credentials file:\n"
                f"{credentials_path}\n\n"
                f"{type(exc).__name__}: {exc}",
            )
            logger.exception(
                "Config editor failed to inspect credentials file: %s",
                credentials_path,
            )
            return None

        if not stat.S_ISREG(credentials_stat.st_mode):
            QMessageBox.critical(
                self.window,
                "Invalid Google credentials",
                "Google credentials path is not a file:\n"
                f"{credentials_path}",
            )
            return None

        return credentials_path

    def _save_config(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        credentials_text = self.credentials_edit.text().strip()
        validated_credentials_path = self._validate_credentials_path(credentials_text)
        if validated_credentials_path is None:
            return

        # Validation uses the resolved path so relative config values are
        # checked against config.json's folder. The saved value intentionally
        # remains the user's original text, preserving portable relative paths
        # such as ".\\serviceworker.json" in source and packaged folders.
        logger.info(
            "Validated credentials path for config save: %s",
            validated_credentials_path,
        )

        self.config_data["credentials"] = credentials_text
        self.config_data["device"] = self.device_combo.currentData()
        self.config_data["language"] = self.language_edit.text().strip() or DEFAULT_LANGUAGE
        self.config_data["rate"] = int(self.rate_spin.value())
        self.config_data["pasteFinal"] = self.paste_final_check.isChecked()
        # Tray and hotkey are operational launch-mode settings. The first
        # settings window focuses on daily dictation preferences, so it
        # preserves these existing values instead of exposing controls that
        # could accidentally hide the tray or disable the configured shortcut.
        self.config_data["tray"] = bool(self.config_data.get("tray", True))
        self.config_data["hotkey"] = bool(self.config_data.get("hotkey", True))
        self.config_data["commandWords"] = self.command_words_check.isChecked()
        self.config_data["appendSpace"] = self.append_space_check.isChecked()
        self.config_data["finalDedupeSeconds"] = float(self.final_dedupe_spin.value())
        self.config_data["idleTimeoutSeconds"] = float(self.idle_timeout_spin.value())
        self.config_data["playStatusSounds"] = self.play_sounds_check.isChecked()
        self.config_data["showListeningIndicator"] = self.show_indicator_check.isChecked()
        self.config_data["listeningIndicatorPosition"] = (
            self.indicator_position_combo.currentData()
        )

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(self.config_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.critical(
                self.window,
                "Unable to save config",
                f"Unable to save config file:\n{self.config_path}\n\n{exc}",
            )
            logger.exception("Config editor failed to save config: %s", self.config_path)
            return

        if self.startup_registry_available:
            startup_operation = (
                "set"
                if self.start_with_windows_check.isChecked()
                else "remove"
            )
            startup_operation_message = (
                "set the Windows startup entry"
                if startup_operation == "set"
                else "remove the Windows startup entry"
            )
            try:
                with winreg.CreateKeyEx(
                    winreg.HKEY_CURRENT_USER,
                    STARTUP_RUN_REGISTRY_PATH,
                    0,
                    winreg.KEY_SET_VALUE,
                ) as key:
                    if self.start_with_windows_check.isChecked():
                        # HKCU Run launches this command after the user signs
                        # in. The value is overwritten on Save so moving the app
                        # folder or editing a different config path updates the
                        # startup command deterministically.
                        winreg.SetValueEx(
                            key,
                            STARTUP_RUN_VALUE_NAME,
                            0,
                            winreg.REG_SZ,
                            self.startup_command,
                        )
                    else:
                        try:
                            winreg.DeleteValue(key, STARTUP_RUN_VALUE_NAME)
                        except FileNotFoundError:
                            # Missing value already means "do not start with
                            # Windows"; this is not a fallback to another
                            # launch method, just an idempotent removal.
                            pass
            except OSError as exc:
                QMessageBox.critical(
                    self.window,
                    "Unable to save startup setting",
                    "Settings were saved to config.json, but Windows startup "
                    f"could not {startup_operation_message} at:\n"
                    f"HKCU\\{STARTUP_RUN_REGISTRY_PATH}\\{STARTUP_RUN_VALUE_NAME}"
                    f"\n\n{type(exc).__name__}: {exc}",
                )
                logger.exception(
                    "Config editor failed to %s Windows startup setting.",
                    startup_operation,
                )
                return

        logger.info("Config editor saved config: %s", self.config_path)
        QMessageBox.information(
            self.window,
            "Settings saved",
            "Settings saved. Restart Win Voice Input to apply changes.",
        )


def run_config_editor(config_path: Path) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv[:1])

    editor = ConfigEditorWindow(config_path)
    editor.show()
    if owns_app:
        return app.exec()
    return 0
