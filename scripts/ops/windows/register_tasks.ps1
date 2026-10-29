# Registers the two scheduled tasks that replace the WSL crontab entries for
# the Binance testnet runner. Run once from Windows PowerShell (not elevated):
#   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1
# Remove with:  Unregister-ScheduledTask -TaskName "TradingAgent-*" -Confirm:$false
param(
    [string]$Distro = "Ubuntu",
    [string]$User = "huythong",
    [string]$Repo = "/home/huythong/.qwenpaw/workspaces/trading",
    [switch]$WhatIf
)
$start = (Get-Date).Date.AddMinutes(1)            # 00:01 local, then every 2 h
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$runner = New-ScheduledTaskAction -Execute "wsl.exe" `
    -Argument "-d $Distro -u $User --exec $Repo/scripts/ops/run_binance_testnet_cycle.sh"
$runnerTrigger = New-ScheduledTaskTrigger -Once -At $start `
    -RepetitionInterval (New-TimeSpan -Hours 2)

$healthScript = "\\wsl.localhost\$Distro" + ($Repo -replace '/', '\') + "\scripts\ops\windows\check_health.ps1"
$health = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$healthScript`" -Distro $Distro -User $User -Repo $Repo"
$healthTrigger = New-ScheduledTaskTrigger -Once -At $start.AddMinutes(19) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

if ($WhatIf) {
    "Runner : wsl.exe $($runner.Arguments)  every 2h from $start"
    "Health : powershell.exe $($health.Arguments)  every 30 min from $($start.AddMinutes(19))"
    exit 0
}
Register-ScheduledTask -TaskName "TradingAgent-BinanceTestnet" -Action $runner `
    -Trigger $runnerTrigger -Settings $settings `
    -Description "Binance testnet execution cycle (scripts/ops/run_binance_testnet_cycle.sh)" -Force
Register-ScheduledTask -TaskName "TradingAgent-AuditHealth" -Action $health `
    -Trigger $healthTrigger -Settings $settings `
    -Description "Alerts when testnet execution runs stop arriving or fail" -Force
