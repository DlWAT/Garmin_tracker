$ErrorActionPreference = "Stop"

$conns = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "No server found on port 5000."
    exit 0
}

$pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pid in $pids) {
    try {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    } catch {}
}

Write-Host "Stopped server on port 5000."