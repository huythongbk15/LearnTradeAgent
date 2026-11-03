# Registers the scheduled tasks that replace the WSL crontab entries for the
# Binance testnet runner, plus the daily check, the weekly paper selection
# run and the weekly NL-P paper log. Run once from Windows PowerShell (not elevated):
#   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1
# -Only DailyCheck registers that task alone and leaves the others untouched.
# Remove with:  Unregister-ScheduledTask -TaskName "TradingAgent-*" -Confirm:$false
param(
    [string]$Distro = "Ubuntu",
    [string]$User = "huythong",
    [string]$Repo = "/home/huythong/.qwenpaw/workspaces/trading",
    [ValidateSet("Runner", "Health", "DailyCheck", "Selection", "NewPerp")]
    [string[]]$Only = @("Runner", "Health", "DailyCheck", "Selection", "NewPerp"),
    [switch]$WhatIf
)
$start = (Get-Date).Date.AddMinutes(1)            # 00:01 local, then every 2 h
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

# conhost --headless: no console window, so nothing on screen can be closed.
# A visible wsl.exe console closed at 00:01 killed the first cycle (0xC000013A).
$runner = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_binance_testnet_cycle.sh"
$runnerTrigger = New-ScheduledTaskTrigger -Once -At $start `
    -RepetitionInterval (New-TimeSpan -Hours 2)

$healthScript = "\\wsl.localhost\$Distro" + ($Repo -replace '/', '\') + "\scripts\ops\windows\check_health.ps1"
$health = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$healthScript`" -Distro $Distro -User $User -Repo $Repo"
$healthTrigger = New-ScheduledTaskTrigger -Once -At $start.AddMinutes(19) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

# Daily at 08:10 local, after the 08:01 cycle: one notification with the result.
$daily = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument ("--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$healthScript`" " +
        "-Distro $Distro -User $User -Repo $Repo -Check scripts/ops/run_daily_check.sh " +
        "-Title `"daily check`" -LogName daily_check.log -Always")
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At $start.AddHours(8).AddMinutes(9)

# Weekly on Monday at 00:07 UTC, read as local time here, so the local weekday can
# differ from Monday. The first run stays ahead of now: registered on a Monday after
# 00:07 UTC the start would already be past, and -StartWhenAvailable could run it at once.
$nowUtc = (Get-Date).ToUniversalTime()
$selectionAt = $nowUtc.Date.AddDays((8 - [int]$nowUtc.DayOfWeek) % 7).AddMinutes(7)
if ($selectionAt -le $nowUtc) { $selectionAt = $selectionAt.AddDays(7) }
$selectionAt = $selectionAt.ToLocalTime()
$selection = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_selection_weekly.sh"
$selectionTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $selectionAt.DayOfWeek -At $selectionAt

# Weekly on Monday at 04:07 UTC, read as local time, the same way.
$newperpAt = $nowUtc.Date.AddDays((8 - [int]$nowUtc.DayOfWeek) % 7).AddHours(4).AddMinutes(7)
if ($newperpAt -le $nowUtc) { $newperpAt = $newperpAt.AddDays(7) }
$newperpAt = $newperpAt.ToLocalTime()
$newperp = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_newperp_weekly.sh"
$newperpTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $newperpAt.DayOfWeek -At $newperpAt

if ($WhatIf) {
    if ($Only -contains "Runner") { "Runner : conhost.exe $($runner.Arguments)  every 2h from $start" }
    if ($Only -contains "Health") { "Health : conhost.exe $($health.Arguments)  every 30 min from $($start.AddMinutes(19))" }
    if ($Only -contains "DailyCheck") { "Daily  : conhost.exe $($daily.Arguments)  daily at $($start.AddHours(8).AddMinutes(9).ToString('HH:mm'))" }
    if ($Only -contains "Selection") { "Select : conhost.exe $($selection.Arguments)  weekly on $($selectionAt.DayOfWeek) at $($selectionAt.ToString('HH:mm')) local (Monday 00:07 UTC), first run $($selectionAt.ToString('yyyy-MM-dd'))" }
    if ($Only -contains "NewPerp") { "NewPerp: conhost.exe $($newperp.Arguments)  weekly on $($newperpAt.DayOfWeek) at $($newperpAt.ToString('HH:mm')) local (Monday 04:07 UTC), first run $($newperpAt.ToString('yyyy-MM-dd'))" }
    exit 0
}
if ($Only -contains "Runner") {
    Register-ScheduledTask -TaskName "TradingAgent-BinanceTestnet" -Action $runner `
        -Trigger $runnerTrigger -Settings $settings `
        -Description "Binance testnet execution cycle (scripts/ops/run_binance_testnet_cycle.sh)" -Force
}
if ($Only -contains "Health") {
    Register-ScheduledTask -TaskName "TradingAgent-AuditHealth" -Action $health `
        -Trigger $healthTrigger -Settings $settings `
        -Description "Alerts when testnet execution runs stop arriving or fail" -Force
}
if ($Only -contains "DailyCheck") {
    Register-ScheduledTask -TaskName "TradingAgent-DailyCheck" -Action $daily `
        -Trigger $dailyTrigger -Settings $settings `
        -Description "Daily read-only report (scripts/ops/run_daily_check.sh); notifies every day" -Force
}
if ($Only -contains "Selection") {
    Register-ScheduledTask -TaskName "TradingAgent-SelectionPaper" -Action $selection `
        -Trigger $selectionTrigger -Settings $settings `
        -Description "Weekly paper selection run, records only (scripts/ops/run_selection_weekly.sh)" -Force
}
if ($Only -contains "NewPerp") {
    Register-ScheduledTask -TaskName "TradingAgent-NewPerpPaper" -Action $newperp `
        -Trigger $newperpTrigger -Settings $settings `
        -Description "Weekly NL-P paper log, records only (scripts/ops/run_newperp_weekly.sh)" -Force
}
