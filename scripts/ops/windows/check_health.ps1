# Runs the WSL health check from Windows and raises a Windows notification
# when it fails, including when WSL itself cannot be reached.
param(
    [string]$Distro = "Ubuntu",
    [string]$User = "huythong",
    [string]$Repo = "/home/huythong/.qwenpaw/workspaces/trading"
)
$ErrorActionPreference = "Continue"
$logDir = Join-Path $env:LOCALAPPDATA "TradingAgent"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "health.log"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$output = & wsl.exe -d $Distro -u $User --exec "$Repo/scripts/ops/check_binance_testnet_health.sh" 2>&1 | Out-String
$code = $LASTEXITCODE
Add-Content -Path $log -Value "$stamp exit=$code $($output.Trim())"
if ($code -eq 0) { exit 0 }

$message = if ($output.Trim()) { $output.Trim() } else { "WSL health check could not run (exit $code)" }
if ($message.Length -gt 200) { $message = $message.Substring(0, 200) }
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $text = $template.GetElementsByTagName("text")
    $text.Item(0).AppendChild($template.CreateTextNode("Trading Agent: testnet health")) | Out-Null
    $text.Item(1).AppendChild($template.CreateTextNode($message)) | Out-Null
    $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
        [Windows.UI.Notifications.ToastNotification]::new($template))
} catch {
    Add-Content -Path $log -Value "$stamp toast failed: $($_.Exception.Message)"
}
exit $code
