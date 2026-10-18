# Create (or replace) the Windows scheduled task that runs Job Hunter every day.
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1 -Times 09:00,13:00,18:00
#   Unregister-ScheduledTask -TaskName "Job Hunter" -Confirm:$false     # remove it
param(
    [string[]]$Times = @("10:00", "18:00"),
    [string]$TaskName = "Job Hunter"
)

$runner = Join-Path $PSScriptRoot "run_scheduled.cmd"
if (-not (Test-Path $runner)) { throw "Missing $runner" }

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runner`""
$triggers = $Times | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
# StartWhenAvailable: if the PC was off or asleep at a run time, run as soon as it's back.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
# Runs as you, only while you're signed in to Windows (no stored password).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
    -Principal $principal -Description "Job Hunter: gather, rank and store new jobs (see logs\scheduled.log)" -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Select-Object @{n = "Task"; e = { $TaskName } }, NextRunTime | Format-Table -AutoSize
