$ErrorActionPreference = "Stop"

& "$PSScriptRoot\stop_background.ps1"
Start-Sleep -Milliseconds 300
& "$PSScriptRoot\run_background.ps1"