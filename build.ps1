param(
    [switch]$Windowed
)

$ErrorActionPreference = "Stop"
$env:PYGAME_HIDE_SUPPORT_PROMPT = "1"

# Build always runs from the project folder so PyInstaller can resolve source
# files and bundled tray/status assets in a predictable way, no matter which
# folder PowerShell was opened from.
Set-Location -LiteralPath $PSScriptRoot

$assetDir = Join-Path $PSScriptRoot "assets"
$pythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host "Missing local Python environment: $pythonExe"
    Write-Host "Create .venv and install requirements.txt before building."
    exit 1
}

$requiredAssets = @("mic.svg", "mic-mute.svg", "start.mp3", "end.mp3")
foreach ($asset in $requiredAssets) {
    # These files are required at runtime because the tray UI renders icons from
    # SVGs and DictationController plays MP3 state cues. The build stops early
    # if they are missing so the packaged exe cannot be created with broken
    # status feedback.
    if (-not (Test-Path -LiteralPath (Join-Path $assetDir $asset))) {
        Write-Host "Missing required app asset: assets\$asset"
        exit 1
    }
}

& $pythonExe -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller is not installed in .venv."
    Write-Host "Install build tools with: .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt"
    exit 1
}

# The first build stays as a console app by default because startup errors and
# Google authentication messages are visible while we validate the package. Use
# -Windowed after testing if you want the tray app without a console window.
$pyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", "WinVoiceInput",
    "--add-data", "assets;assets",
    "--hidden-import", "pystray._win32",
    "--collect-submodules", "google.cloud.speech_v1",
    "--collect-submodules", "google.api_core",
    "src\voice_input.py"
)

if ($Windowed) {
    $pyInstallerArgs = @("--windowed") + $pyInstallerArgs
}

& $pythonExe -m PyInstaller @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$distDir = Join-Path $PSScriptRoot "dist\WinVoiceInput"
if (-not (Test-Path -LiteralPath $distDir)) {
    Write-Host "Build finished but output folder was not found: $distDir"
    exit 1
}

# Do not copy config.json automatically because it can contain local credential
# paths. The example file is copied instead so the packaged folder documents the
# expected settings shape without moving secrets into dist by accident.
Copy-Item `
    -LiteralPath (Join-Path $PSScriptRoot "config.example.json") `
    -Destination (Join-Path $distDir "config.example.json") `
    -Force

Write-Host "Build complete:"
Write-Host (Join-Path $distDir "WinVoiceInput.exe")
Write-Host "Create config.json inside the same folder before daily use."
