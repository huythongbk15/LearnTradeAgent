# Registers the scheduled tasks that replace the WSL crontab entries for the
# Binance testnet runner, plus the daily check. Run once from Windows
# PowerShell (not elevated):
#   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1
# -Only DailyCheck registers that task alone and leaves the others untouched.
# Remove with:  Unregister-ScheduledTask -TaskName "TradingAgent-*" -Confirm:$false
param(
    [string]$Distro = "Ubuntu",
    [string]$User = "huythong",
    [string]$Repo = "/home/huythong/.qwenpaw/workspaces/trading",
    [ValidateSet("Runner", "Health", "DailyCheck")]
    [string[]]$Only = @("Runner", "Health", "DailyCheck"),
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

if ($WhatIf) {
    if ($Only -contains "Runner") { "Runner : conhost.exe $($runner.Arguments)  every 2h from $start" }
    if ($Only -contains "Health") { "Health : conhost.exe $($health.Arguments)  every 30 min from $($start.AddMinutes(19))" }
    if ($Only -contains "DailyCheck") { "Daily  : conhost.exe $($daily.Arguments)  daily at $($start.AddHours(8).AddMinutes(9).ToString('HH:mm'))" }
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
