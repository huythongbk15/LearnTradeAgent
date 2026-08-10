#!/usr/bin/env python3
"""
Cron runner: chạy live trading Enhanced MA + notify Telegram (chỉ khi có lệnh).

Cách dùng:
    python scripts/live_cron_runner.py            # dry-run + notify
    python scripts/live_cron_runner.py --execute  # Alpaca Paper + notify

Telegram config: env TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (hoặc .env)
"""
import sys
import os
import subprocess
import json
from datetime import datetime
sys.path.insert(0, 'src')

from dotenv import load_dotenv
load_dotenv('.env')


def send_telegram(text: str) -> bool:
    """Gửi 1 message Telegram trực tiếp, trả True nếu gửi được."""
    bot = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot or not chat:
        print("  [telegram] SKIP — TELEGRAM_BOT_TOKEN/CHAT_ID chưa set trong .env")
        return False
    import urllib.request
    payload = json.dumps({
        "chat_id": chat,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = json.loads(r.read())["ok"]
        print(f"  [telegram] {'✅ sent' if ok else '❌ api error'}")
        return ok
    except Exception as e:
        print(f"  [telegram] ❌ {e}")
        return False


def main():
    execute = "--execute" in sys.argv
    if execute and os.getenv("TRADING_EXECUTION_ENABLED", "false").lower() != "true":
        print("REFUSED: TRADING_EXECUTION_ENABLED is not true", file=sys.stderr)
        sys.exit(3)
    if execute and os.getenv("TRADING_MODE", "paper").lower() != "paper":
        print("REFUSED: only TRADING_MODE=paper is supported", file=sys.stderr)
        sys.exit(3)

    # 1. Chạy live script, capture output
    cmd = [sys.executable, "scripts/live_enhanced_ma.py"]
    if execute:
        cmd.append("--execute")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== {ts} | live_enhanced_ma {'PAPER EXECUTE' if execute else 'DRY-RUN'} ===")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    out = result.stdout + result.stderr

    # 2. Parse orders từ output (dòng '✅ Order: ...' hoặc '❌ Order failed: ...' sau EXECUTION)
    lines = out.splitlines()
    trades = []
    in_exec = False
    started = False
    for ln in lines:
        s = ln.strip()
        if "🎯 EXECUTION" in s:
            in_exec = True
            started = True
            continue
        if not in_exec:
            continue
        if s.startswith("--- ") and "→" in s:
            trades.append({"header": s, "status": "pending"})
        elif s.startswith("[DRY-RUN] Would"):
            if trades and trades[-1]["status"] == "pending":
                trades[-1]["status"] = "dry-run"
        elif s.startswith("✅ Order:"):
            if trades and trades[-1]["status"] == "pending":
                trades[-1]["status"] = "filled"
                trades[-1]["detail"] = s
        elif s.startswith("❌ Order failed:") or s.startswith("❌"):
            if trades and trades[-1]["status"] == "pending":
                trades[-1]["status"] = "failed"
                trades[-1]["detail"] = s

    print(out)

    # 3. Notify Telegram: chi khi có lệnh thật sự (fill) hoặc lỗi nghiêm trọng
    filled = [t for t in trades if t["status"] == "filled"]
    failed = [t for t in trades if t["status"] == "failed"]

    if filled:
        lines_msg = []
        for t in filled:
            # "--- BTC/USDT → BUY 0.461618 ---"
            head = t["header"].replace("---", "").split("→")[0].strip()
            side = t["header"].split("→")[1].strip().rstrip("---").strip()
            lines_msg.append(f"{emoji(side)} {head}: `{side}`")
        msg = (
            f"🤖 *Live Trading — {len(filled)} lệnh*\n"
            + "\n".join(lines_msg)
            + "\n\nXem dashboard: https://app.alpaca.markets/paper/dashboard/positions"
        )
        send_telegram(msg)

    if failed:
        fails = "\n".join(f"• {t['header']} — {t.get('detail','')}" for t in failed)
        send_telegram(f"⚠️ *Live trading: {len(failed)} lệnh thất bại*\n{fails}")

    if not trades:
        print("  → Không có lệnh nào cần xử lý (signals giữ nguyên)")

    # 4. In output (cho cron log)
    print(out[-2200:] if len(out) > 2200 else out)

    # Exit code
    if failed:
        sys.exit(2)
    sys.exit(0)


def emoji(side: str) -> str:
    return "🟢" if "BUY" in side.upper() else "🔴" if "SELL" in side.upper() else "🔵"

if __name__ == "__main__":
    main()
