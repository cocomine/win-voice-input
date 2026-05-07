# Windows Voice Input MVP

First test version for Cantonese dictation on Windows using Google Speech-to-Text.

## What This Version Does

- Reads audio from your microphone.
- Streams it to Google Speech-to-Text.
- Prints interim and final transcripts in the terminal.
- Pastes final transcripts into the active Windows app.
- Shows a system tray icon with Idle / Listening / Stopping status.
- Shows a small listening status window while listening.
- Uses `mic.svg` in green while listening, and automatically recolors `mic-mute.svg` for Windows light or dark system UI while not listening.
- Plays `start.mp3` when listening begins and `end.mp3` when listening stops.
- Uses `Ctrl+Alt+V` or the tray menu to start or pause listening.
- Stops the current listening session after 5 seconds without recognized text.
- Voice commands are disabled by default, so recognized text is pasted as-is.
- Reads optional settings from `config.json`; without a device setting, it uses the Windows default input device.
- Can be packaged as a Windows `.exe` with the tray SVG assets included.
- Defaults to Hong Kong Cantonese: `yue-Hant-HK`.

Interim text is only shown in the terminal. Only final text is pasted.

## Setup

1. Install Python 3.11 or newer from <https://www.python.org/downloads/windows/>.
2. Create or select a Google Cloud project.
3. Enable the Cloud Speech-to-Text API.
4. Create a service account key JSON file.
5. In PowerShell, point Google auth to that JSON file:

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS="D:\path\to\service-account.json"
```

6. Install dependencies:

```powershell
pip install -r requirements.txt
```

This workspace already has a local `.venv` with the dependencies installed, so you can use the included scripts directly.

## Project Layout

- `voice_input.py` - command-line entry point and argument parsing.
- `app_config.py` - shared defaults and settings objects.
- `audio_capture.py` - microphone capture and audio chunk generator.
- `dictation_session.py` - Google Speech-to-Text streaming session.
- `windows_text_output.py` - Windows clipboard and keyboard paste integration.
- `dictation_controller.py` - shared start/stop lifecycle for hotkey and tray UI.
- `global_hotkey.py` - global `Ctrl+Alt+V` Win32 hotkey listener.
- `hotkey_app.py` - global `Ctrl+Alt+V` start/pause control.
- `tray_app.py` - system tray icon, status display, and tray menu.
- `listening_indicator.py` - floating listening status window.
- `text_processing.py` - optional command-word conversion and duplicate filtering.
- `config.example.json` - optional local settings template.
- `mic.svg`, `mic-mute.svg`, `start.mp3`, `end.mp3` - required tray/status assets.
- `build.ps1` - PyInstaller build script for creating `WinVoiceInput.exe`.
- `requirements-build.txt` - build-only dependency list.

## Run

Daily use with `config.json`:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1
```

Shortcut note: current builds use `Ctrl+Alt+V`. Earlier test builds used
`Ctrl+Alt+Space`, which no longer toggles listening.

Create `config.json` by copying `config.example.json`, then edit values you want to remember. If `device` is `null` or missing, the app uses the Windows default input device.

Common `config.json` settings:

- `idleTimeoutSeconds`: seconds without recognized text before listening stops. Use `0` to disable.
- `playStatusSounds`: `true` plays `start.mp3` and `end.mp3`; `false` keeps state changes silent.
- `showListeningIndicator`: `true` shows the floating listening status window; `false` hides it.
- `listeningIndicatorPosition`: `bottom-center`, `bottom-left`, `bottom-right`, `top-center`, `top-left`, or `top-right`.

List microphones:

```powershell
powershell -ExecutionPolicy Bypass -File .\list-devices.ps1
```

## Build Windows Exe

Install the build-only dependency into the same `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

Build the first test package:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The output is:

```text
dist\WinVoiceInput\WinVoiceInput.exe
```

The default build keeps a console window open so startup errors, microphone
selection, and Google authentication messages are visible during testing. After
the package is stable, build without the console window:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Windowed
```

For daily packaged use, place a `config.json` beside `WinVoiceInput.exe` in
`dist\WinVoiceInput`. The app reads that file directly, so the packaged exe does
not need `run-dictation.ps1`.

Start listening with the default microphone:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json"
```

After it starts, a tray icon appears in the Windows notification area. Click into Notepad, Word, a browser text box, or any other target app. Press `Ctrl+Alt+V` or use the tray menu to start listening. Press `Ctrl+Alt+V` again, or choose Pause from the tray menu, to stop the current session.

On startup, the console prints the input device it will use. If no device is configured, it prints the current Windows default input device.

While paused, the app does not record microphone audio and does not send audio to Google.

If Google Speech-to-Text does not return any recognized text for 5 seconds, the current listening session stops automatically and returns to idle.

While listening, a small status window appears near the bottom center of the
desktop work area. It does not depend on the active app exposing a Windows
caret, so it remains visible in browser-rendered text fields as well as Notepad.

Use a specific microphone:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -Device 1
```

Use another language:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -Language en-US
```

Console-only mode:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -ConsoleOnly
```

Use the old console hotkey mode without tray:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -NoTray
```

Start immediately without tray or global hotkey:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -NoTray -NoHotkey
```

Change the idle timeout:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -IdleTimeoutSeconds 8
```

Stop with `Ctrl+C`.

## Optional Spoken Commands

Voice commands are disabled by default. To enable them for a test run:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -EnableCommandWords
```

- `換行` or `新一行` -> newline
- `逗號` -> `，`
- `句號` -> `。`
- `問號` -> `？`
- `感嘆號` -> `！`
- `空格` -> space
- `刪除` or `退格` -> backspace

## Notes

- Google streaming sessions have time limits, so this prototype is meant for short tests.
- If your microphone fails at `16000` Hz, try `--rate 48000`.
- Paste mode uses the Windows clipboard, so your clipboard content will be replaced by the latest final transcript.
- Duplicate protection can be enabled with `-FinalDedupeSeconds 0.8`. The current script default is `0`.
- Idle auto-stop is enabled by default with `-IdleTimeoutSeconds 5`; use `0` to disable it.
- The packaged exe reads `config.json` from the same folder as `WinVoiceInput.exe`.
