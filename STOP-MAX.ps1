$ErrorActionPreference = 'Stop'
$botFilePath = Join-Path $PSScriptRoot 'bot.py'
$botProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($botFilePath) -and -not $_.CommandLine.Contains('--demo') -and -not $_.CommandLine.Contains('--check')
})
foreach ($botProcess in $botProcesses) {
    Stop-Process -Id $botProcess.ProcessId
}
Write-Output "Stopped MAX processes for this project: $($botProcesses.Count)"
