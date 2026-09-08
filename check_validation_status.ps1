# Monitor orchestration progress
Write-Host "`n=== FORGEAI VALIDATION STATUS ===" -ForegroundColor Cyan

$logFile = "orchestration.log"

if (Test-Path $logFile) {
    Write-Host "`n📊 RECENT PROGRESS:"
    Get-Content $logFile -Tail 20 | ForEach-Object { Write-Host $_ }
} else {
    Write-Host "⏳ Test still initializing..." -ForegroundColor Yellow
}

# Check test results
if (Test-Path "test_results.json") {
    $results = Get-Content test_results.json | ConvertFrom-Json
    $working = @($results | Where-Object { $_.working -eq $true }).Count
    $total = $results.Count
    $pct = if ($total -gt 0) { [math]::Round(($working/$total)*100, 1) } else { 0 }

    Write-Host "`n✅ RESULTS: $working/$total working ($pct%)"
} else {
    Write-Host "`n⏳ No results yet..."
}

Write-Host "`n💡 TIP: Run 'Get-Content orchestration.log -Tail 50 -Wait' for live monitoring"
Write-Host "`n===============================`n"
