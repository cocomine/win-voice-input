param(
    [string]$Credentials = "",
    [int]$Device = 1,
    [string]$Language = "yue-Hant-HK",
    [int]$Rate = 16000,
    [switch]$ConsoleOnly,
    [switch]$NoHotkey,
    [switch]$EnableCommandWords,
    [switch]$NoCommandWords,
    [switch]$AppendSpace,
    [double]$FinalDedupeSeconds = 0
)

if ($Credentials -ne "") {
    $env:GOOGLE_APPLICATION_CREDENTIALS = $Credentials
}

if (-not $env:GOOGLE_APPLICATION_CREDENTIALS) {
    Write-Host "Please set Google credentials first:"
    Write-Host '.\run-dictation.ps1 -Credentials ".\eveapp-320519-17a1cbb68e48.json"'
    exit 1
}

$arguments = @(
    ".\voice_input.py",
    "--device", $Device,
    "--language", $Language,
    "--rate", $Rate,
    "--final-dedupe-seconds", $FinalDedupeSeconds
)

# Paste mode remains separate from hotkey mode: hotkey controls when audio is
# streamed to Google, while paste mode controls where final transcripts go.
if (-not $ConsoleOnly) {
    $arguments += "--paste-final"
}

# V3 starts idle by default so no microphone audio is sent to Google until the
# user explicitly presses Ctrl+Alt+Space.
if (-not $NoHotkey) {
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
