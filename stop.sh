#!/bin/bash
cd "$(dirname "$0")"
if [ -f scheduler.pid ]; then
  kill "$(cat scheduler.pid)" && echo "scheduler stopped"
  rm scheduler.pid
else
  pkill -f "scheduler.py" && echo "scheduler stopped" || echo "not running"
fi
