param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$taskName = 'FX_auto hourly research'
$launcher = Join-Path $PSScriptRoot 'Start-FXResearch.ps1'
$powerShell = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
$owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$next = (Get-Date).Date.AddHours((Get-Date).Hour).AddMinutes(47)
if ($next -le (Get-Date)) { $next = $next.AddHours(1) }

if ($DryRun) {
    [pscustomobject]@{ Task = $taskName; Account = 'Current Windows user';
        NextLocalTime = $next; Interval = '1 hour'; Launcher = $launcher }
    return
}

$gh = (Get-Command gh.exe -ErrorAction Stop).Source
& $gh auth status --hostname github.com *> $null
if ($LASTEXITCODE -ne 0) { throw 'Sign in to GitHub CLI before registration.' }

$action = New-ScheduledTaskAction -Execute $powerShell -Argument (
    '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -Scheduled' -f $launcher)
$trigger = New-ScheduledTaskTrigger -Once -At $next -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal
