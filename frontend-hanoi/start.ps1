$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = (Get-Command python).Source }
$previewUrl = 'http://127.0.0.1:8081'
try {
    $running = Invoke-WebRequest -Uri "$previewUrl/" -UseBasicParsing -TimeoutSec 2
    if ($running.StatusCode -eq 200) { Start-Process $previewUrl; return }
} catch {}
$stdoutPath = Join-Path $PSScriptRoot 'preview.log'
$stderrPath = Join-Path $PSScriptRoot 'preview-error.log'
Start-Process -FilePath $pythonPath -ArgumentList '-u', 'frontend-hanoi/serve.py', '--start-backend' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $ready = Invoke-WebRequest -Uri "$previewUrl/api/places" -UseBasicParsing -TimeoutSec 2
        if ($ready.StatusCode -eq 200) { Start-Process $previewUrl; Write-Output "Ready: $previewUrl"; return }
    } catch {}
}
throw "Preview could not start. Read $stderrPath"
