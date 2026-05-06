# Windows Voice Input MVP

First test version for Cantonese dictation on Windows using Google Speech-to-Text.

## What This Version Does

- Reads audio from your microphone.
- Streams it to Google Speech-to-Text.
- Prints interim and final transcripts in the terminal.
- Pastes final transcripts into the active Windows app.
- Starts idle and uses `Ctrl+Alt+Space` to start or pause listening.
- Voice commands are disabled by default, so recognized text is pasted as-is.
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
- `hotkey_app.py` - global `Ctrl+Alt+Space` start/pause control.
- `text_processing.py` - optional command-word conversion and duplicate filtering.

## Run

List microphones:

```powershell
powershell -ExecutionPolicy Bypass -File .\list-devices.ps1
```

Start listening with the default microphone:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json"
```

After it starts, click into Notepad, Word, a browser text box, or any other target app. Press `Ctrl+Alt+Space` to start listening. Press `Ctrl+Alt+Space` again to pause.

While paused, the app does not record microphone audio and does not send audio to Google.

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

Start immediately without the global hotkey:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -NoHotkey
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
- The next version can add a tray icon and settings file.
