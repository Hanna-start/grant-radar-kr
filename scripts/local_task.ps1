# Only manages this checkout's task. Existing legacy tasks are left intact.
param(
    [ValidateSet("status", "enable", "disable")][string]$Mode = "status",
    [string]$PythonPath
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$Root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$sha = [Security.Cryptography.SHA256]::Create()
try { $digest = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant())) }
finally { $sha.Dispose() }
$Suffix = ([BitConverter]::ToString($digest) -replace "-", "").Substring(0,12)
$TaskName = "GrantRadar-Local-$Suffix"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Mode -eq "status") {
    if (-not $Task) { Write-Output "자동 메일 꺼짐"; exit 0 }
    $Info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Output "상태: $($Task.State) / 다음 실행: $($Info.NextRunTime) / 마지막 결과 코드: $($Info.LastTaskResult)"
    exit 0
}
if ($Mode -eq "disable") {
    if ($Task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    Write-Output "이 폴더의 자동 메일을 껐습니다."
    exit 0
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "Python executable not found." }
$Script = Join-Path $Root "scripts\weekly_run.ps1"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`" -PythonPath `"$PythonPath`""
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 09:00
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Grant Radar local weekly report" -Force | Out-Null
Write-Output "매주 월요일 오전 9시 자동 메일을 켰습니다. PC 로그인·인터넷 연결이 필요합니다."
