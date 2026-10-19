# Cinelume setup for Windows (phase 7.4).
# Run from the repository root:  powershell -ExecutionPolicy Bypass -File scripts/setup.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "=== Cinelume setup ===" -ForegroundColor Yellow

# --- 1. Python ---------------------------------------------------------------
try {
    $pythonVersion = (& python --version) 2>$null
    Write-Host "Python: $pythonVersion"
    if ($pythonVersion -notmatch "Python 3\.(11|12|13)") {
        throw "Python 3.11-3.13 is required."
    }
} catch {
    Write-Host "Installing Python via winget..." -ForegroundColor Cyan
    winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    & python --version
}

# --- 2. FFmpeg ---------------------------------------------------------------
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($null -eq $ffmpeg) {
    Write-Host "Installing FFmpeg via winget (Gyan.FFmpeg)..." -ForegroundColor Cyan
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
} else {
    Write-Host "FFmpeg: $($ffmpeg.Source)"
}

$ffmpegCheck = & ffmpeg -hide_banner -filters 2>$null | Select-String "afftdn|ass |loudnorm"
if (-not $ffmpegCheck) {
    Write-Warning "FFmpeg is present but may be missing required filters (afftdn/ass/loudnorm)."
}

# --- 3. Virtual environment --------------------------------------------------
if (-not (Test-Path "$Root\venv\Scripts\python.exe")) {
    Write-Host "Creating the virtual environment..." -ForegroundColor Cyan
    python -m venv venv
} else {
    Write-Host "Virtual environment already exists."
}

$venvPython = "$Root\venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip --quiet
Write-Host "Installing backend dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install -e "backend[asr,dev]" --quiet

# --- 4. Frontend -------------------------------------------------------------
$node = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $node) {
    Write-Host "Node.js not found - installing via winget..." -ForegroundColor Cyan
    winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

if (Test-Command node) {
    Write-Host "Building the frontend..." -ForegroundColor Cyan
    Push-Location "$Root\frontend"
    npm install
    npm run build
    Pop-Location
} else {
    Write-Warning "Node.js is unavailable; the UI will not be served at /. Start it with 'npm run dev' in frontend/."
}

# --- 5. Optional: Whisper model ----------------------------------------------
$answer = Read-Host "Pre-download the 'small' Whisper model now? (~750 MB) [y/N]"
if ($answer -match "^[Yy]") {
    & $venvPython -c "from app.services.transcribe import get_model_manager; get_model_manager().start('small')"
}

Write-Host ""
Write-Host "Setup complete. Start the app with scripts\start.bat" -ForegroundColor Green
