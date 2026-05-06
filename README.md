# Windows Voice Input MVP

First test version for Cantonese dictation on Windows using Google Speech-to-Text.

## What This Version Does

- Reads audio from your microphone.
- Streams it to Google Speech-to-Text.
- Prints interim and final transcripts in the terminal.
- Defaults to Hong Kong Cantonese: `yue-Hant-HK`.

This first version does not type into other apps yet. It is for checking recognition quality and latency.

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

## Run

List microphones:

```powershell
powershell -ExecutionPolicy Bypass -File .\list-devices.ps1
```

Start listening with the default microphone:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json"
```

Use a specific microphone:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -Device 1
```

Use another language:

```powershell
powershell -ExecutionPolicy Bypass -File .\run-dictation.ps1 -Credentials "D:\path\to\service-account.json" -Language en-US
```

Stop with `Ctrl+C`.

## Notes

- Google streaming sessions have time limits, so this prototype is meant for short tests.
- If your microphone fails at `16000` Hz, try `--rate 48000`.
- The next version can add a hotkey and paste final text into the active Windows app.
