#!/bin/bash
# 센싱 프로세스 죽으면 자동 재시작 (영상 최우선)
SENSING_DIR="/home/jetson/Desktop/sensing_code"
PID_FILE="/tmp/sensing_main.pid"
LOG="$SENSING_DIR/logs/watchdog.log"

while true; do
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ! ps -p "$PID" > /dev/null 2>&1; then
            echo "$(date '+%H:%M:%S') 센싱 프로세스 죽음! 재시작..." >> "$LOG"
            bash "$SENSING_DIR/switch_sensing.sh" >> "$LOG" 2>&1
        fi
    fi
    sleep 30
done
