$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

function Resolve-PythonExe {
    $candidates = @(
        Join-Path $repoRoot ".venv\Scripts\python.exe",
        Join-Path $repoRoot ".venv312\Scripts\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return "py" }
    throw "Python executable not found. Create a venv (.venv or .venv312) or install the 'py' launcher."
}

function Stop-ServerOnPort5000 {
    $conns = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
    if ($conns) {
        $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($pid in $pids) {
            try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}

# If already running, do nothing.
$existing = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Server already running on http://127.0.0.1:5000 (port 5000)."
    exit 0
}

$pythonExe = Resolve-PythonExe
$appPath = Join-Path $repoRoot "app.py"
if (!(Test-Path $appPath)) { throw "app.py not found at $appPath" }

# Start hidden background process
Start-Process -FilePath $pythonExe -ArgumentList @($appPath) -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null

Start-Sleep -Milliseconds 400
Write-Host "Started Garmin Tracker in background: http://127.0.0.1:5000"