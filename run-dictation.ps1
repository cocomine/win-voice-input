param(
    [string]$ConfigPath = ".\config.json",
    [string]$Credentials = "",
    [object]$Device = $null,
    [string]$Language = "",
    [object]$Rate = $null,
    [switch]$PasteFinal,
    [switch]$ConsoleOnly,
    [switch]$Tray,
    [switch]$NoTray,
    [switch]$Hotkey,
    [switch]$NoHotkey,
    [switch]$EnableCommandWords,
    [switch]$NoCommandWords,
    [switch]$AppendSpace,
    [switch]$NoAppendSpace,
    [object]$FinalDedupeSeconds = $null,
    [object]$IdleTimeoutSeconds = $null
)

# Built-in defaults mirror the Python defaults and previous script behavior.
# They are used only when config.json omits a value and the user does not pass a
# command-line override.
$configWasExplicit = $PSBoundParameters.ContainsKey("ConfigPath")
$settings = [ordered]@{
    Credentials = ""
    Device = $null
    Language = "yue-Hant-HK"
    Rate = 16000
    PasteFinal = $true
    Tray = $true
    Hotkey = $true
    CommandWords = $false
    AppendSpace = $false
    FinalDedupeSeconds = 0
    IdleTimeoutSeconds = 5
}

if (Test-Path -LiteralPath $ConfigPath) {
    try {
        # config.json is optional. When present, it changes only the fields it
        # explicitly contains; missing fields keep the defaults above. This keeps
        # partial local configuration readable and avoids copying boilerplate.
        $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    } catch {
        Write-Host "Failed to read config file: $ConfigPath"
        Write-Host $_.Exception.Message
        exit 1
    }

    if ($null -ne $config.credentials) { $settings.Credentials = [string]$config.credentials }
    if ($null -ne $config.device) { $settings.Device = $config.device }
    if ($null -ne $config.language) { $settings.Language = [string]$config.language }
    if ($null -ne $config.rate) { $settings.Rate = $config.rate }
    if ($null -ne $config.pasteFinal) { $settings.PasteFinal = [bool]$config.pasteFinal }
    if ($null -ne $config.tray) { $settings.Tray = [bool]$config.tray }
    if ($null -ne $config.hotkey) { $settings.Hotkey = [bool]$config.hotkey }
    if ($null -ne $config.commandWords) { $settings.CommandWords = [bool]$config.commandWords }
    if ($null -ne $config.appendSpace) { $settings.AppendSpace = [bool]$config.appendSpace }
    if ($null -ne $config.finalDedupeSeconds) { $settings.FinalDedupeSeconds = $config.finalDedupeSeconds }
    if ($null -ne $config.idleTimeoutSeconds) { $settings.IdleTimeoutSeconds = $config.idleTimeoutSeconds }
} elseif ($configWasExplicit) {
    # A manually supplied config path is expected to be real. Stopping here
    # prevents the wrapper and Python entry point from silently running with
    # different settings.
    Write-Host "Config file does not exist: $ConfigPath"
    exit 1
}

# Command-line arguments are applied last because an explicit invocation should
# be able to test a different device or mode without editing config.json.
if ($Credentials -ne "") { $settings.Credentials = $Credentials }
if ($PSBoundParameters.ContainsKey("Device")) { $settings.Device = $Device }
if ($Language -ne "") { $settings.Language = $Language }
if ($PSBoundParameters.ContainsKey("Rate")) { $settings.Rate = $Rate }
if ($PasteFinal) { $settings.PasteFinal = $true }
if ($ConsoleOnly) { $settings.PasteFinal = $false }
if ($Tray) { $settings.Tray = $true }
if ($NoTray) { $settings.Tray = $false }
if ($Hotkey) { $settings.Hotkey = $true }
if ($NoHotkey) { $settings.Hotkey = $false }
if ($EnableCommandWords) { $settings.CommandWords = $true }
if ($NoCommandWords) { $settings.CommandWords = $false }
if ($AppendSpace) { $settings.AppendSpace = $true }
if ($NoAppendSpace) { $settings.AppendSpace = $false }
if ($PSBoundParameters.ContainsKey("FinalDedupeSeconds")) { $settings.FinalDedupeSeconds = $FinalDedupeSeconds }
if ($PSBoundParameters.ContainsKey("IdleTimeoutSeconds")) { $settings.IdleTimeoutSeconds = $IdleTimeoutSeconds }

if ($settings.Credentials -ne "") {
    # Google client libraries read this environment variable when creating the
    # SpeechClient. The script sets it only when config or command line provides
    # a path, so an existing shell-level credential remains usable.
    $env:GOOGLE_APPLICATION_CREDENTIALS = $settings.Credentials
}

if (-not $env:GOOGLE_APPLICATION_CREDENTIALS) {
    # Running without credentials would fail later inside the Google client. The
    # script stops here so the user sees the missing setup step immediately.
    Write-Host "Please set Google credentials first:"
    Write-Host 'Create config.json from config.example.json or pass -Credentials ".\eveapp-320519-17a1cbb68e48.json"'
    exit 1
}

# Build an argument array instead of a single command string. PowerShell passes
# each item as one argument, which avoids quoting bugs in file paths and keeps
# optional flags easy to review.
$arguments = @(
    ".\src\voice_input.py",
    "--language", $settings.Language,
    "--rate", $settings.Rate,
    "--final-dedupe-seconds", $settings.FinalDedupeSeconds,
    "--idle-timeout-seconds", $settings.IdleTimeoutSeconds
)

if ((Test-Path -LiteralPath $ConfigPath) -or $configWasExplicit) {
    # Python now reads config.json directly so the same behavior works after
    # packaging into an exe. Passing the path keeps PowerShell runs and exe runs
    # aligned, especially when a non-default config path is used for testing.
    $arguments += "--config"
    $arguments += $ConfigPath
}

if ($null -ne $settings.Device) {
    # Omitting --device is intentional: sounddevice then opens the Windows
    # default input device. Passing a device only happens when config or command
    # line explicitly chooses one.
    $arguments += "--device"
    $arguments += $settings.Device
}

# Paste mode remains separate from tray/hotkey mode: tray and hotkey control
# when audio is streamed to Google, while paste mode controls where final
# transcripts go.
if ($settings.PasteFinal) {
    $arguments += "--paste-final"
}

if ($settings.Tray) {
    $arguments += "--tray"
} elseif ($settings.Hotkey) {
    $arguments += "--hotkey"
}

# Python's default is command words disabled. The wrapper only passes a flag
# when enabling command words, so the Python default remains the single source
# of truth for the disabled state.
if ($settings.CommandWords) {
    $arguments += "--command-words"
}

if ($settings.AppendSpace) {
    $arguments += "--append-space"
}

.\.venv\Scripts\python.exe @arguments
