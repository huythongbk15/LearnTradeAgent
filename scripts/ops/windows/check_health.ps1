# Runs a WSL check from Windows and raises a Windows notification when it
# fails, including when WSL itself cannot be reached. The defaults are the
# 30-min health check; the daily check passes its own script and -Always.
param(
    [string]$Distro = "Ubuntu",
    [string]$User = "huythong",
    [string]$Repo = "/home/huythong/.qwenpaw/workspaces/trading",
    [string]$Check = "scripts/ops/check_binance_testnet_health.sh",
    [string]$Title = "testnet health",
    [string]$LogName = "health.log",
    [switch]$Always                      # notify on success too
)
$ErrorActionPreference = "Continue"
$logDir = Join-Path $env:LOCALAPPDATA "TradingAgent"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir $LogName
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# PowerShell 5.1 wraps each stderr line of a native command in an ErrorRecord;
# "$_" turns it back into the plain line the check printed.
$lines = & wsl.exe -d $Distro -u $User --exec "$Repo/$Check" 2>&1 |
    ForEach-Object { "$_" }
$code = $LASTEXITCODE
$output = ($lines -join " ").Trim()
Add-Content -Path $log -Value "$stamp exit=$code $output"
if ($code -eq 0 -and -not $Always) { exit 0 }

$message = if ($output) { $output } else { "WSL check $Check could not run (exit $code)" }
if ($message.Length -gt 200) { $message = $message.Substring(0, 200) }
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $text = $template.GetElementsByTagName("text")
    $text.Item(0).AppendChild($template.CreateTextNode("Trading Agent: $Title")) | Out-Null
    $text.Item(1).AppendChild($template.CreateTextNode($message)) | Out-Null
    $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
        [Windows.UI.Notifications.ToastNotification]::new($template))
} catch {
    Add-Content -Path $log -Value "$stamp toast failed: $($_.Exception.Message)"
}
exit $code
