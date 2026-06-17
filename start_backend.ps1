# Restart backend server cleanly
$ErrorActionPreference = "Stop"

# Kill existing python processes
Get-Process python,python3.11 -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3

# Clean pycache
Get-ChildItem "$PSScriptRoot\backend" -Recurse -Filter "__pycache__" -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

# Reinstall curl_cffi to fix TLS DLL
python3.11 -m pip install curl_cffi --force-reinstall --no-cache-dir -q 2>&1 | Out-Null

# Start uvicorn (no reload to avoid DLL issues)
Set-Location $PSScriptRoot
Start-Process -FilePath "python3.11" `
    -ArgumentList "-m","uvicorn","backend.main:app","--host","0.0.0.0","--port","8000" `
    -WindowStyle Hidden `
    -WorkingDirectory $PSScriptRoot

Start-Sleep -Seconds 4
Write-Host "Backend started on :8000"
