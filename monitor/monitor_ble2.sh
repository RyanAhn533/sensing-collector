#!/bin/bash
SENSING_DIR="/home/jetson/Desktop/sensing_code"
DATA_DIR="$SENSING_DIR/data"
PID_FILE="/tmp/sensing_main.pid"
ENV_PY="/home/jetson/anaconda3/envs/sensing/bin/python"
MAIN_PY="$SENSING_DIR/main.py"
LOG="$SENSING_DIR/logs/monitor_ble2.log"

log() { echo "$(date +%H:%M:%S) $1" | tee -a "$LOG"; }

restart_sensing() {
    PARTICIPANT=$1
    log "[$PARTICIPANT] 재시작 시작..."
    kill $(cat "$PID_FILE" 2>/dev/null) 2>/dev/null
    sleep 3
    while [ $(ps aux | grep main.py | grep -v grep | wc -l) -gt 0 ]; do
        killall -9 python 2>/dev/null; sleep 2
    done
    # 동글 리셋
    DONGLE=$(find /sys/bus/usb/devices/ -maxdepth 2 -name idVendor -exec grep -l 0456 {} 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
    if [ -n "$DONGLE" ]; then
        sudo -n sh -c "echo 0 > ${DONGLE}/authorized" 2>/dev/null
        sleep 3
        sudo -n sh -c "echo 1 > ${DONGLE}/authorized" 2>/dev/null
        sleep 5
    fi
    cd "$SENSING_DIR"
    PARTICIPANT_ID="$PARTICIPANT" setsid "$ENV_PY" -u "$MAIN_PY" > "$SENSING_DIR/logs/${PARTICIPANT}_$(date +%H%M).log" 2>&1 &
    echo $! > "$PID_FILE"
    log "[$PARTICIPANT] 재시작 PID=$(cat $PID_FILE)"
    sleep 35
    NEW_LATEST=$(ls -t "$DATA_DIR/$PARTICIPANT/" 2>/dev/null | head -1)
    V="X"
    ls "$DATA_DIR/$PARTICIPANT/$NEW_LATEST"/video_main* > /dev/null 2>&1 && V="OK"
    log "[$PARTICIPANT] 재시작 후 영상=$V"
}

check() {
    [ ! -f "$PID_FILE" ] && return 2
    PID=$(cat "$PID_FILE")
    ps -p "$PID" > /dev/null 2>&1 || { log "프로세스 죽음!"; return 2; }

    PARTICIPANT=$(ls -td "$DATA_DIR"/C[0-9][0-9][0-9] 2>/dev/null | head -1 | xargs basename)
    [ -z "$PARTICIPANT" ] && return 1

    # ppg.csv가 있는 가장 최근 폴더 찾기 (전체 검색)
    LAST_PPG=$(find "$DATA_DIR/$PARTICIPANT/" -name "ppg.csv" -size +0c -printf "%T@ %p\n" 2>/dev/null | sort -rn | head -1 | awk "{print \$2}")

    if [ -z "$LAST_PPG" ]; then
        # ppg.csv 자체가 한 번도 안 만들어진 경우
        # main.py 시작 시간 확인
        START_AGE=$(( $(date +%s) - $(stat -c%Y "$PID_FILE") ))
        if [ $START_AGE -gt 180 ]; then
            log "[$PARTICIPANT] ppg.csv 없음 (시작 후 ${START_AGE}초), 재시작"
            restart_sensing "$PARTICIPANT"
        else
            log "[$PARTICIPANT] 아직 대기 중 (${START_AGE}초)"
        fi
        return 0
    fi

    AGE=$(( $(date +%s) - $(stat -c%Y "$LAST_PPG") ))
    LATEST=$(ls -t "$DATA_DIR/$PARTICIPANT/" 2>/dev/null | head -1)
    VIDEO="X"
    ls "$DATA_DIR/$PARTICIPANT/$LATEST"/video_main* > /dev/null 2>&1 && VIDEO="OK"

    if [ $AGE -lt 150 ]; then
        log "[$PARTICIPANT] $LATEST | v=$VIDEO ppg=OK(${AGE}s) BLE정상"
    else
        log "[$PARTICIPANT] $LATEST | v=$VIDEO ppg=STALE(${AGE}s) BLE끊김! 재시작"
        restart_sensing "$PARTICIPANT"
    fi
    return 0
}

log "=== BLE 모니터 v2 시작 (2분 간격) ==="
sleep 60
check
while true; do
    sleep 120
    [ ! -f "$PID_FILE" ] && { log "종료"; break; }
    check
    [ $? -eq 2 ] && { log "프로세스 없음, 종료"; break; }
done
