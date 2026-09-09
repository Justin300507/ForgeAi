# Kill existing processes
Get-Process | Where-Object {$_.Name -eq "python" -and $_.CommandLine -match "uvicorn"} | Stop-Process -Force 2>$null
Start-Sleep -Seconds 2

# Set encoding
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = "C:\Users\jerry\ForgeAi\backend"

# Change to backend directory and start server
Set-Location C:\Users\jerry\ForgeAi\backend
Write-Host "Starting ForgeAI server with UTF-8 encoding..."
Write-Host "PYTHONIOENCODING=$env:PYTHONIOENCODING"

# Start server
& python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info
