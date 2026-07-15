#!/bin/bash
cd "$(dirname "$0")"
nohup .venv/bin/python scheduler.py >> runs/scheduler.log 2>&1 &
echo $! > scheduler.pid
echo "scheduler started, PID=$(cat scheduler.pid), log=runs/scheduler.log"
