# Windows 작업 스케줄러에 주간 실행을 등록한다 (사용자가 직접 실행).
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
#
# - 매주 월요일 09:00, 사용자 로그인 세션에서 실행
# - StartWhenAvailable: 그 시각에 PC가 꺼져 있었으면 다음 켜진 직후 실행
# - 실행 제한 1시간 (보통 2~3분)
# 해제: Unregister-ScheduledTask -TaskName "GrantRadar Weekly" -Confirm:$false
# 확인: Get-ScheduledTask -TaskName "GrantRadar Weekly" | Get-ScheduledTaskInfo

$Root = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $Root "scripts\weekly_run.ps1"
$TaskName = "GrantRadar Weekly"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`"" `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 09:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Grant Radar KR 주간 수집·판정·메일 (LLM 호출 없음)" -Force

Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo | Select-Object NextRunTime, LastRunTime, LastTaskResult
