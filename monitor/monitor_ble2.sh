#!/bin/bash
# BLE 모니터 v3 — 워치 자동 재시작 + 동글 리셋 통합
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
LOG_DIR="$SCRIPT_DIR/logs"
LOG="$LOG_DIR/monitor_ble2.log"
PID_FILE="$LOG_DIR/sensing.pid"

# conda 환경
if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniforge3/etc/profile.d/conda.sh"
    conda activate sensing 2>/dev/null
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
    conda activate sensing 2>/dev/null
fi

log() { echo "$(date +%H:%M:%S) $1" | tee -a "$LOG"; }

reset_dongle() {
    DONGLE=$(for d in /sys/bus/usb/devices/*/idVendor; do
        v=$(cat "$d" 2>/dev/null)
        [ "$v" = "0456" ] && echo "$(dirname "$d")" && break
    done)
    if [ -n "$DONGLE" ]; then
        sudo -n sh -c "echo 0 > ${DONGLE}/authorized" 2>/dev/null
        sleep 2
        sudo -n sh -c "echo 1 > ${DONGLE}/authorized" 2>/dev/null
        sleep 5
        log "동글 리셋: $DONGLE"
    fi
}

restart_watch_standalone() {
    PARTICIPANT=$1
    log "[$PARTICIPANT] 워치 재시작..."
    pkill -f watch_standalone 2>/dev/null
    sleep 2
    reset_dongle
    cd "$SCRIPT_DIR"
    setsid python3 -u "$SCRIPT_DIR/monitor/watch_standalone.py" "$PARTICIPANT" \
        > "$LOG_DIR/watch_standalone.log" 2>&1 < /dev/null &
    log "[$PARTICIPANT] 워치 재시작 PID=$!"
}

check() {
    # main.py PID 확인
    MPID=$(pgrep -f 'python.*main.py' | head -1)
    [ -z "$MPID" ] && { log "main.py 없음"; return 2; }

    # 참가자 폴더
    PARTICIPANT=$(ls -td "$DATA_DIR"/C[0-9][0-9][0-9] 2>/dev/null | head -1 | xargs basename)
    [ -z "$PARTICIPANT" ] && return 1

    # ppg.csv 최신 확인
    LAST_PPG=$(find "$DATA_DIR/$PARTICIPANT/" -name "ppg.csv" -size +0c -printf "%T@ %p\n" 2>/dev/null | sort -rn | head -1 | awk '{print $2}')

    if [ -z "$LAST_PPG" ]; then
        START_AGE=$(( $(date +%s) - $(stat -c%Y /proc/$MPID) ))
        if [ $START_AGE -gt 180 ]; then
            log "[$PARTICIPANT] ppg 없음 (${START_AGE}s), 워치 재시작"
            restart_watch_standalone "$PARTICIPANT"
        else
            log "[$PARTICIPANT] 대기 중 (${START_AGE}s)"
        fi
        return 0
    fi

    AGE=$(( $(date +%s) - $(stat -c%Y "$LAST_PPG") ))
    LATEST=$(ls -t "$DATA_DIR/$PARTICIPANT/" 2>/dev/null | head -1)
    VIDEO="X"
    ls "$DATA_DIR/$PARTICIPANT/$LATEST"/video_main* > /dev/null 2>&1 && VIDEO="OK"

    if [ $AGE -lt 150 ]; then
        log "[$PARTICIPANT] $LATEST | v=$VIDEO ppg=OK(${AGE}s)"
    else
        log "[$PARTICIPANT] $LATEST | v=$VIDEO ppg=STALE(${AGE}s) → 워치 재시작"
        restart_watch_standalone "$PARTICIPANT"
    fi
    return 0
}

log "=== BLE 모니터 v3 시작 (2분 간격) ==="
sleep 60
check
while true; do
    sleep 120
    MPID=$(pgrep -f 'python.*main.py' | head -1)
    [ -z "$MPID" ] && { log "main.py 종료, 모니터 종료"; break; }
    check
done
