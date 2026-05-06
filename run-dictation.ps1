param(
    [string]$Credentials = "",
    [int]$Device = 1,
    [string]$Language = "yue-Hant-HK",
    [int]$Rate = 16000
)

if ($Credentials -ne "") {
    $env:GOOGLE_APPLICATION_CREDENTIALS = $Credentials
}

if (-not $env:GOOGLE_APPLICATION_CREDENTIALS) {
    Write-Host "Please set Google credentials first:"
    Write-Host '.\run-dictation.ps1 -Credentials ".\eveapp-320519-17a1cbb68e48.json"'
    exit 1
}

.\.venv\Scripts\python.exe .\voice_input.py --device $Device --language $Language --rate $Rate
