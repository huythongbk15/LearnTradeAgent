# Registers the scheduled tasks that replace the WSL crontab entries for the
# Binance testnet runner, plus the daily check, the weekly paper selection
# run, the weekly NL-P paper log, the forced selling paper log and the hourly perp
# demo book. Run once from Windows PowerShell (not elevated):
#   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1
# -Only DailyCheck registers that task alone and leaves the others untouched.
# FlowFlush (the 5-minute demo runs of books FLOW01 and KRW) is never in the default list: it is
# registered only by name, with -Only FlowFlush.
# Remove with:  Unregister-ScheduledTask -TaskName "TradingAgent-*" -Confirm:$false
param(
    [string]$Distro = "Ubuntu",
    [string]$User = "huythong",
    [string]$Repo = "/home/huythong/.qwenpaw/workspaces/trading",
    [ValidateSet("Runner", "Health", "DailyCheck", "Selection", "NewPerp", "FlowBid", "PerpDemo", "FlowFlush")]
    [string[]]$Only = @("Runner", "Health", "DailyCheck", "Selection", "NewPerp", "FlowBid", "PerpDemo"),
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

# Every 5 minutes from 00:03 local. The wrapper skips :50 of an even UTC hour to
# :06 of the odd hour, when the testnet cycle runs, and skips while a run is going.
$flowbid = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_flowbid.sh"
$flowbidTrigger = New-ScheduledTaskTrigger -Once -At $start.AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

# Every hour at :15 local. The wrapper also keeps out of :50 of an even UTC hour
# to :06 of the odd hour; a listing's entry window is 2 h, so an hourly run sees it.
$perpdemo = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_perp_demo.sh"
$perpdemoTrigger = New-ScheduledTaskTrigger -Once -At $start.AddMinutes(14) `
    -RepetitionInterval (New-TimeSpan -Hours 1)

# Every 5 minutes from 00:02 local (:02, :07, :12, :17, ...): the 5-minute modes of books FLOW01, then KRW.
# Two minutes after each 5-minute stamp, so a row published up to about 84 s late is seen,
# and no run starts from :13 to :16, so a signed run has 3 minutes before the hourly run. Most runs only read
# public data and sign nothing. The wrapper keeps out of :50 of an even UTC hour to
# :06 of the odd hour, and skips while the hourly perp demo run holds its lock.
$flowflush = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_flow_flush.sh"
$flowflushTrigger = New-ScheduledTaskTrigger -Once -At $start.AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

if ($WhatIf) {
    if ($Only -contains "Runner") { "Runner : conhost.exe $($runner.Arguments)  every 2h from $start" }
    if ($Only -contains "Health") { "Health : conhost.exe $($health.Arguments)  every 30 min from $($start.AddMinutes(19))" }
    if ($Only -contains "DailyCheck") { "Daily  : conhost.exe $($daily.Arguments)  daily at $($start.AddHours(8).AddMinutes(9).ToString('HH:mm'))" }
    if ($Only -contains "Selection") { "Select : conhost.exe $($selection.Arguments)  weekly on $($selectionAt.DayOfWeek) at $($selectionAt.ToString('HH:mm')) local (Monday 00:07 UTC), first run $($selectionAt.ToString('yyyy-MM-dd'))" }
    if ($Only -contains "NewPerp") { "NewPerp: conhost.exe $($newperp.Arguments)  weekly on $($newperpAt.DayOfWeek) at $($newperpAt.ToString('HH:mm')) local (Monday 04:07 UTC), first run $($newperpAt.ToString('yyyy-MM-dd'))" }
    if ($Only -contains "FlowBid") { "FlowBid: conhost.exe $($flowbid.Arguments)  every 5 min from $($start.AddMinutes(2))" }
    if ($Only -contains "PerpDemo") { "PerpDemo: conhost.exe $($perpdemo.Arguments)  every hour from $($start.AddMinutes(14))" }
    if ($Only -contains "FlowFlush") { "FlowFlush: conhost.exe $($flowflush.Arguments)  every 5 min from $($start.AddMinutes(1))" }
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
if ($Only -contains "FlowBid") {
    Register-ScheduledTask -TaskName "TradingAgent-FlowBidPaper" -Action $flowbid `
        -Trigger $flowbidTrigger -Settings $settings `
        -Description "Forced selling paper log every 5 min, records only (scripts/ops/run_flowbid.sh)" -Force
}
if ($Only -contains "PerpDemo") {
    Register-ScheduledTask -TaskName "TradingAgent-PerpDemo" -Action $perpdemo `
        -Trigger $perpdemoTrigger -Settings $settings `
        -Description "Hourly perp demo books on Binance Demo Trading (pair book with its mainnet shadow ledger, LV, LEARN, FLOW02, FLOW01, KRW), book HOLD and the H-LEARN-11 paper log (scripts/ops/run_perp_demo.sh)" -Force
}
if ($Only -contains "FlowFlush") {
    Register-ScheduledTask -TaskName "TradingAgent-FlowFlushDemo" -Action $flowflush `
        -Trigger $flowflushTrigger -Settings $settings `
        -Description "Books FLOW01 and KRW on Binance Demo Trading every 5 min; each signs only when an exit is due, an order is unsettled or an event is in its entry window (scripts/ops/run_flow_flush.sh)" -Force
}
