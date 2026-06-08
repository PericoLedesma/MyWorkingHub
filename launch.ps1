# SmartRecorder launcher — loads .env, activates venv, starts app

$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match "^\s*[^#]" } | ForEach-Object {
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) {
            [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        }
    }
} else {
    Write-Host "No .env file found. Create one from .env.example or set keys manually." -ForegroundColor Yellow
}

$venv = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $venv)) {
    Write-Host "Virtual environment not found. Run: python -m venv .venv && pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

& $venv
python (Join-Path $PSScriptRoot "main.py")
