#!/usr/bin/env python3
"""CLI chẩn đoán LLMPool multi-provider.

Usage:
    python scripts/llm_pool_cli.py status          # trạng thái providers + quota
    python scripts/llm_pool_cli.py health           # probe từng provider (gọi thật)
    python scripts/llm_pool_cli.py ask "question"   # gửi 1 request qua pool
    python scripts/llm_pool_cli.py ask --provider groq "question"  # ép 1 provider

Load .env.local nếu tồn tại.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Load .env.local trước khi import trading (cần env vars)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
env_file = PROJECT_ROOT / ".env.local"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

sys.path.insert(0, str(PROJECT_ROOT))

from trading_agent.llm.pool import create_llm_pool  # noqa: E402


def print_status(pool) -> None:
    st = pool.status()
    print(f"=== LLMPool status ({st['today']}) ===")
    print(f"{'provider':<15} {'enabled':<8} {'needs-key':<10} {'quota-left':<12} {'cooldown':<9} error")
    for p in st["providers"]:
        cooldown = "yes" if p["in_cooldown"] else "no"
        print(
            f"{p['name']:<15} {str(p['enabled']):<8} {str(p['requires_key']):<10} "
            f"{p['quota_remaining']:<12} {cooldown:<9} {p['last_error'][:40]}"
        )
    print()


async def print_health(pool) -> None:
    print("=== Health probe (1 request mỗi provider) ===")
    results = await pool.health()
    for name, r in results.items():
        if r["ok"]:
            print(f"  {name:<15} OK    {r['latency_ms']}ms  {r['preview']!r}")
        else:
            print(f"  {name:<15} FAIL  {r['reason']}")
    print()


async def do_ask(pool, question: str, provider: str | None = None) -> None:
    if provider:
        target = next((p for p in pool.providers if p.name == provider), None)
        if target is None:
            print(f"Unknown provider: {provider}")
            return
        # Ép chỉ dùng provider đó bằng cách tắt những cái khác
        for p in pool.providers:
            if p.name != provider:
                p.enabled = False
        print(f"=== Forcing provider: {provider} ===")
    print(f"Q: {question}\n")
    try:
        text = await pool.chat(
            [{"role": "user", "content": question}],
            max_tokens=512,
        )
        print(f"A [{pool.last_provider}]:\n{text}\n")
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLMPool diagnostic CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="trạng thái providers + quota")
    h = sub.add_parser("health", help="probe từng provider")
    h.add_argument("--timeout", type=int, default=30)

    ask = sub.add_parser("ask", help="gửi request qua pool")
    ask.add_argument("question", help="câu hỏi")
    ask.add_argument("--provider", default=None, help="ép dùng 1 provider")

    args = parser.parse_args()
    pool = create_llm_pool()

    if args.cmd == "status":
        print_status(pool)
    elif args.cmd == "health":
        asyncio.run(print_health(pool))
    elif args.cmd == "ask":
        asyncio.run(do_ask(pool, args.question, args.provider))


if __name__ == "__main__":
    main()
