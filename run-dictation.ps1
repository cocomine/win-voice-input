param(
    [string]$Credentials = "",
    [int]$Device = 1,
    [string]$Language = "yue-Hant-HK",
    [int]$Rate = 16000,
    [switch]$ConsoleOnly,
    [switch]$NoTray,
    [switch]$NoHotkey,
    [switch]$EnableCommandWords,
    [switch]$NoCommandWords,
    [switch]$AppendSpace,
    [double]$FinalDedupeSeconds = 0,
    [double]$IdleTimeoutSeconds = 5
)

if ($Credentials -ne "") {
    # Google client libraries read this environment variable when creating the
    # SpeechClient. The script sets it only when the caller provides a path, so
    # an existing shell-level credential remains usable.
    $env:GOOGLE_APPLICATION_CREDENTIALS = $Credentials
}

if (-not $env:GOOGLE_APPLICATION_CREDENTIALS) {
    # Running without credentials would fail later inside the Google client. The
    # script stops here so the user sees the missing setup step immediately.
    Write-Host "Please set Google credentials first:"
    Write-Host '.\run-dictation.ps1 -Credentials ".\eveapp-320519-17a1cbb68e48.json"'
    exit 1
}

# Build an argument array instead of a single command string. PowerShell passes
# each item as one argument, which avoids quoting bugs in file paths and keeps
# optional flags easy to review.
$arguments = @(
    ".\voice_input.py",
    "--device", $Device,
    "--language", $Language,
    "--rate", $Rate,
    "--final-dedupe-seconds", $FinalDedupeSeconds,
    "--idle-timeout-seconds", $IdleTimeoutSeconds
)

# Paste mode remains separate from hotkey mode: hotkey controls when audio is
# streamed to Google, while paste mode controls where final transcripts go.
if (-not $ConsoleOnly) {
    $arguments += "--paste-final"
}

# V4 uses the system tray by default. Tray mode includes its own hotkey listener
# and status UI, so the standalone console hotkey flag is only added when tray
# mode is explicitly disabled.
if (-not $NoTray) {
    $arguments += "--tray"
} elseif (-not $NoHotkey) {
    $arguments += "--hotkey"
}

if ($EnableCommandWords) {
    $arguments += "--command-words"
}

if ($NoCommandWords) {
    $arguments += "--no-command-words"
}

if ($AppendSpace) {
    $arguments += "--append-space"
}

.\.venv\Scripts\python.exe @arguments
