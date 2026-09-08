# Monitor test progress in real-time

$targetFile = "test_results.json"
$goalApps = 80
$lastCount = 0
$checkInterval = 5

Write-Host "=" * 80
Write-Host "MONITORING 100-APP TEST PROGRESS"
Write-Host "=" * 80
Write-Host "Goal: $goalApps+ working apps (Forge Score >= 80)"
Write-Host "Checking every $checkInterval seconds..."
Write-Host ""

$startTime = Get-Date

while ($true) {
    if (Test-Path $targetFile) {
        try {
            $json = Get-Content $targetFile -Raw | ConvertFrom-Json
            if ($json) {
                $working = @($json | Where-Object { $_.working -eq $true }).Count
                $total = @($json).Count

                if ($total -gt $lastCount) {
                    $pct = if ($total -gt 0) { [math]::Round($working / $total * 100) } else { 0 }
                    $elapsed = ((Get-Date) - $startTime).TotalMinutes

                    if ($working -ge $goalApps) {
                        Write-Host "🎉 SUCCESS!" -ForegroundColor Green
                        Write-Host "$working / $total apps working ($pct%)" -ForegroundColor Green
                        Write-Host "Time: $([math]::Round($elapsed, 1)) minutes" -ForegroundColor Green
                        exit 0
                    } else {
                        $status = "✅" if $working -ge 50 else "⚠️"
                        Write-Host "$status $working / $total working ($pct%) | Elapsed: $([math]::Round($elapsed, 1))m"
                    }

                    $lastCount = $total
                }
            }
        } catch {
            # JSON parse error, continue
        }
    }

    Start-Sleep -Seconds $checkInterval
}
