#!/usr/bin/env python3
"""
Local backup script — chạy trên máy bạn.
Backup: TimescaleDB (pg_dump), Redis (rdb), config folder.
Lưu tại: ./backups/YYYYMMDD_HHMMSS/
Giữ: 7 bản gần nhất.
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime
import shutil

# === CONFIG (sửa cho máy bạn) ===
BACKUP_ROOT = Path("./backups")
DB_CONTAINER = (
    "trading-timescaledb"  # tên container TimescaleDB (docker-compose container_name)
)
REDIS_CONTAINER = "trading-redis"  # tên container Redis
DB_USER = "trading"
DB_NAME = "trading_market_data"
CONFIG_DIR = Path("./config")  # thư mục config local
KEEP_LAST = 7
# =================================


def run(cmd, check=True):
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  ✗ FAILED: {result.stderr}")
        sys.exit(1)
    if result.stdout:
        print(f"  {result.stdout.strip()}")
    return result


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Backup {timestamp} ===")
    print(f"Output: {backup_dir}")

    # 1. TimescaleDB dump
    print("\n1. Dumping TimescaleDB...")
    dump_file = backup_dir / f"timescaledb_{timestamp}.dump"
    run(f"docker exec {DB_CONTAINER} pg_dump -U {DB_USER} -Fc {DB_NAME} > {dump_file}")

    # 2. Redis RDB
    print("\n2. Dumping Redis...")
    rdb_file = backup_dir / f"redis_{timestamp}.rdb"
    tmp_rdb = f"/tmp/redis_{timestamp}.rdb"
    run(f"docker exec {REDIS_CONTAINER} redis-cli SAVE")
    run(f"docker exec {REDIS_CONTAINER} redis-cli --rdb {tmp_rdb}")
    run(f"docker cp {REDIS_CONTAINER}:{tmp_rdb} {rdb_file}")
    run(f"docker exec {REDIS_CONTAINER} rm -f {tmp_rdb}")

    # 3. Config folder
    print("\n3. Copying config...")
    if CONFIG_DIR.exists():
        run(f"cp -r {CONFIG_DIR} {backup_dir}/")
    else:
        print(f"  ⚠ Config dir not found: {CONFIG_DIR}")

    # 4. Show size
    print("\n4. Backup size:")
    run(f"du -sh {backup_dir}")

    # 5. Retention
    print(f"\n5. Keeping last {KEEP_LAST} backups...")
    all_backups = sorted(
        BACKUP_ROOT.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for old in all_backups[KEEP_LAST:]:
        print(f"  Removing {old.name}")
        shutil.rmtree(old)

    print(f"\n✅ Done! Backup saved to: {backup_dir}")


if __name__ == "__main__":
    main()
