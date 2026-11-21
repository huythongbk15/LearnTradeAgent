# Perp R0: D10 host setup before stage B

[PERP_LIVE_R0.md](PERP_LIVE_R0.md) D10 puts the live key out of agent reach: a separate WSL user owns the live checkout and the key file, and the Windows task runs as that user. The stage A review found that on this host the agent's user (`huythong`) could still reach that user. These are the commands that close those paths. **The operator types every command.** Agents write this page and never run these commands, since they change system and security settings.

Facts read on 2026-11-20:
- `/etc/wsl.conf` holds only `[user] default=huythong`. Interop and automount are on, so `/mnt/c` is writable and `wsl.exe -u root` works from inside WSL.
- `huythong` is in `sudo` and `docker`. Docker is not installed in the distro, and no script in `scripts/` or `src/` calls `docker`.
- No WSL-side script in `scripts/` or `src/` calls `powershell.exe`, `wsl.exe` or `cmd.exe`. Turning interop off breaks no scheduled run.
- Two Windows tasks run a PowerShell file straight from the agent-writable checkout (`TradingAgent-AuditHealth` and `TradingAgent-DailyCheck`, file `scripts/ops/windows/check_health.ps1`). That file runs as the Windows user, who can become root in WSL. This is the path step 4 closes.
- The other tasks run `wsl.exe -u huythong --exec <script>`. Those scripts run as `huythong` and reach nothing more than `huythong` already can, so they stay as they are.
- `register_tasks.ps1` already registers the live task as user `perplive` from `/home/perplive/trading-r0` (`-Only PerpLive`).

## Order and time
1. Steps 1-3 inside WSL, at any time.
2. Step 4 in Windows PowerShell as Administrator.
3. Step 5 ends every WSL process, this agent session and any scheduled run included. Run it between :25 and :45 of an **odd** UTC hour (local 08:25-08:45, 10:25, ...). That keeps it clear of the testnet cycle (from :01 of the odd UTC hour), the hourly Demo run at :15 and the 5-minute Demo runs at :02 and every 5 minutes after. First check that no run is going (`pgrep -af 'run_|live_'` prints nothing).
4. Step 6 checks the result.

Become root in WSL from a Windows PowerShell window (this works before and after step 5, because it starts from Windows):
```powershell
wsl.exe -d Ubuntu -u root
```

## 1. The live user (as root in WSL)
```bash
adduser --disabled-password --gecos "" perplive
```
```bash
chmod 700 /home/perplive
```
`perplive` has no password and is in no extra group. It is entered only from Windows with `wsl.exe -d Ubuntu -u perplive`.

## 2. The live checkout, pinned to a tag
1. As `huythong`, in the main checkout, tag the current `master`, pack it, and write the exact package versions the perp tests ran with (ccxt 4.5.90 among them):
   ```bash
   cd ~/.qwenpaw/workspaces/trading && git fetch origin && git tag r0-b1 origin/master && git bundle create /var/tmp/r0-b1.bundle r0-b1 && .venv/bin/python -m pip freeze --exclude-editable > /var/tmp/r0-b1.req && chmod 644 /var/tmp/r0-b1.bundle /var/tmp/r0-b1.req
   ```
2. As `perplive` (`wsl.exe -d Ubuntu -u perplive`), clone it and build its own venv with those versions:
   ```bash
   cd ~ && git clone --branch r0-b1 /var/tmp/r0-b1.bundle trading-r0
   ```
   ```bash
   python3 -m venv ~/trading-r0/.venv && ~/trading-r0/.venv/bin/pip install -r /var/tmp/r0-b1.req && ~/trading-r0/.venv/bin/pip install --no-deps -e ~/trading-r0
   ```
3. As root, remove the two files:
   ```bash
   rm /var/tmp/r0-b1.bundle /var/tmp/r0-b1.req
   ```
A later code change follows K-change: a new tag, a new bundle, `git fetch` and `git checkout <tag>` as `perplive`, one attended cycle.

## 3. The key, the env file and the shared notice page (as `perplive`)
1. Generate the Ed25519 key pair. Only the public half leaves the PC:
   ```bash
   install -d -m 700 ~/.keys && (umask 077 && openssl genpkey -algorithm ed25519 -out ~/.keys/r0_ed25519.pem)
   ```
   ```bash
   openssl pkey -in ~/.keys/r0_ed25519.pem -pubout
   ```
   Paste the printed public key into Binance when you create the sub-account's API key ("self-generated", Ed25519). For stage B, give it **read permission only**. For stage C, edit the same key to add futures trading, never withdrawal (D9).
2. The env file, mode 600:
   ```bash
   install -m 600 /dev/null ~/.perp_live.env
   ```
   ```bash
   echo "LIVE_SAFETY_HMAC_KEY=$(openssl rand -hex 32)" >> ~/.perp_live.env
   ```
   Then add these names with an editor, values typed by you only:
   - `BINANCE_LIVE_FUTURES_API_KEY`: the key Binance shows for that public key.
   - `BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE=/home/perplive/.keys/r0_ed25519.pem`
   - `PERP_LIVE_KRW_USD`: the venue minimum (PERP_LIVE_R0.md section 3).
   - `PERP_LIVE_TELEGRAM_BOT_TOKEN`, `PERP_LIVE_TELEGRAM_CHAT_ID`, `PERP_LIVE_DEADMAN_URL` (https).
   - Leave out `PERP_LIVE_ALLOW_ORDERS` and `PERP_LIVE_STAGE` for stage B. Without `PERP_LIVE_ALLOW_ORDERS=true` the run never sends an order, whatever the key allows.
   - Never set `BINANCE_LIVE_FUTURES_API_SECRET`: the venue refuses to start with it.
3. The shared Upbit page (item 5b), which the Demo roots read and only the live user writes:
   ```bash
   install -d -m 755 /var/tmp/perp_notice_share
   ```

## 4. Windows tasks never run a file the agent can write (PowerShell as Administrator)
Copy the health script to a folder only administrators can write, then point both tasks at the copy. Read the copy before running it; it must equal `scripts/ops/windows/check_health.ps1` on `master`.
```powershell
$dst = "C:\ProgramData\TradingAgent"; New-Item -ItemType Directory -Force -Path $dst | Out-Null; icacls $dst /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "Users:(OI)(CI)RX"
```
```powershell
Copy-Item "\\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\check_health.ps1" "C:\ProgramData\TradingAgent\check_health.ps1"
```
```powershell
foreach ($n in "TradingAgent-AuditHealth", "TradingAgent-DailyCheck") { $t = Get-ScheduledTask -TaskName $n; $a = $t.Actions[0]; $a.Arguments = $a.Arguments -replace '-File "[^"]*check_health\.ps1"', '-File "C:\ProgramData\TradingAgent\check_health.ps1"'; Set-ScheduledTask -TaskName $n -Action $a | Out-Null; (Get-ScheduledTask -TaskName $n).Actions[0].Arguments }
```
Each printed line must show `-File "C:\ProgramData\TradingAgent\check_health.ps1"`. Running `register_tasks.ps1` again for these two tasks puts the old path back, so repeat this step after it. Register the live task (`-Only PerpLive`) only when stage B starts.

## 5. Interop off, automount off, `huythong` out of `sudo` and `docker` (as root in WSL)
```bash
printf '[user]\ndefault=huythong\n\n[interop]\nenabled=false\nappendWindowsPath=false\n\n[automount]\nenabled=false\n' > /etc/wsl.conf && cat /etc/wsl.conf
```
```bash
gpasswd -d huythong sudo; gpasswd -d huythong docker
```
Then in Windows PowerShell, at the time given above:
```powershell
wsl.exe --shutdown
```
The next scheduled task starts WSL again with the new settings.

What changes for you:
- Root in WSL is reached only from Windows: `wsl.exe -d Ubuntu -u root`. `sudo` no longer works as `huythong`.
- Inside WSL, Windows programs (`explorer.exe`, `code .`, opening a browser from a script) no longer start. Windows still reaches the WSL files through `\\wsl.localhost\Ubuntu`.
- `/mnt/c` is gone inside WSL. The workspace page's health-log line (`scripts/ops/workspace_status.py`, which reads `/mnt/c/Users/*/AppData/Local/TradingAgent/health.log`) then shows no log; the log itself is still written on Windows.
- To undo: as root, restore the old `/etc/wsl.conf`, `usermod -aG sudo,docker huythong`, then `wsl.exe --shutdown`.

## 6. Checks (as `huythong` in WSL, after step 5)
Each command must fail or print nothing:
```bash
wsl.exe -u root true; ls /mnt/c; sudo -n true; ls /home/perplive; id -nG | tr ' ' '\n' | grep -xE 'sudo|docker'
```
And as `perplive`, each line must be true:
```bash
stat -c '%a %U %n' ~ ~/.keys ~/.keys/r0_ed25519.pem ~/.perp_live.env /var/tmp/perp_notice_share; git -C ~/trading-r0 describe --tags
```
Expected modes and owners: `700 perplive` for the home and `.keys`, `600 perplive` for the key and the env file, `755 perplive` for the notice folder; the tag is `r0-b1`.

Write the date and "done" in PERP_LIVE_R0.md's Stage A record under the D10 blocker. Stage B then starts with `register_tasks.ps1 -Only PerpLive`.
