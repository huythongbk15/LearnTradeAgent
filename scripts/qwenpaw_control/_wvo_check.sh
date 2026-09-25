#!/bin/bash
echo "Waiting 300s before checks..."
sleep 300
echo "=== Sleep done at $(date) ==="
echo ""
echo "=== Counting report.json files ==="
for d in /dev/shm/wvo_eth /dev/shm/wvo_sol; do
  echo "Directory: $d"
  if [ -d "$d" ]; then
    cnt=$(find "$d" -name 'report.json' 2>/dev/null | wc -l)
    echo "  report.json count: $cnt"
    find "$d" -name 'report.json' 2>/dev/null
  else
    echo "  (directory does not exist)"
  fi
  echo ""
done
echo "=== Listing subdirectories ==="
for d in /dev/shm/wvo_eth /dev/shm/wvo_sol; do
  echo "Directory: $d"
  if [ -d "$d" ]; then
    find "$d" -mindepth 1 -maxdepth 1 -type d 2>/dev/null
  else
    echo "  (directory does not exist)"
  fi
  echo ""
done
echo "=== Checking wfo_decision.json ==="
echo "=== wfo_decision.json locations ==="
find /dev/shm/wvo_eth /dev/shm/wvo_sol -name 'wfo_decision.json' 2>/dev/null || echo "(none found)"
echo "=== Done at $(date) ==="
