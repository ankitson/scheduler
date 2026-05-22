<#
.SYNOPSIS
  Register a Windows scheduled task. Low-level: the caller (scheduler.py) builds the
  full executable + argument string and the trigger options; this script just talks to
  the Task Scheduler API. Daily or fixed-interval triggers are supported.

.NOTES
  Use -IntervalHours / -IntervalMinutes for a repeating trigger; otherwise -At gives a
  daily trigger. StartWhenAvailable runs a slot missed during sleep on the next wake.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Name,
    [Parameter(Mandatory = $true)] [string] $ExePath,
    [Parameter(Mandatory = $true)] [string] $ArgString,
    [string] $At = "03:30",
    [int] $IntervalHours = 0,
    [int] $IntervalMinutes = 0,
    [string] $TaskFolder = "Scheduler",
    [int] $TimeLimitHours = 1,
    [string] $WorkDir = (Get-Location).Path
)

$ErrorActionPreference = 'Stop'

$action = New-ScheduledTaskAction -Execute $ExePath -Argument $ArgString -WorkingDirectory $WorkDir

if ($IntervalHours -gt 0 -or $IntervalMinutes -gt 0) {
    # Repeat indefinitely on a fixed interval, aligned to midnight so slots land on the clock.
    # [TimeSpan]::MaxValue serialises to an out-of-range duration, so build the repetition
    # with a placeholder duration and null it out to mean "Indefinitely".
    $interval = New-TimeSpan -Hours $IntervalHours -Minutes $IntervalMinutes
    $rep = (New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
        -RepetitionInterval $interval -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
    $rep.Duration = $null
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date
    $trigger.Repetition = $rep
    $parts = @()
    if ($IntervalHours -gt 0) { $parts += "$($IntervalHours)h" }
    if ($IntervalMinutes -gt 0) { $parts += "$($IntervalMinutes)m" }
    $when = "every " + ($parts -join " ")
} else {
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $when = "daily at $At"
}

# StartWhenAvailable catches up a slot missed while the PC was asleep, on next wake
# (without waking the PC itself).
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $TimeLimitHours)

$taskName = "$TaskFolder\$Name"
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Scheduled run of '$Name' (wrapped by run_job.py for logging + retries)" -Force | Out-Null

Write-Output "Registered scheduled task: $taskName  ($when)"
Write-Output "Working dir: $WorkDir"
Write-Output "Executes: $ExePath $ArgString"
