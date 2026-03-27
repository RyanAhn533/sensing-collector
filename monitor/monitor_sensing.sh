#!/bin/bash
# 센싱 모니터링 — 영상 감시 + 워치 끊기면 자동 재연결
SENSING_DIR="/home/jetson/Desktop/sensing_code"
DATA_DIR="$SENSING_DIR/data"
PID_FILE="/tmp/sensing_main.pid"
ENV_PY="/home/jetson/anaconda3/envs/sensing/bin/python"
MAIN_PY="$SENSING_DIR/main.py"
LOG="$SENSING_DIR/logs/monitor.log"

log() { echo "$(date '+%H:%M:%S') $1" | tee -a "$LOG"; }

notify() {
    DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority \
    zenity --warning --title="$1" --text="$2" --timeout=10 2>/dev/null &
}

check_sensing() {
    if [ ! -f "$PID_FILE" ]; then return 1; fi
    PID=$(cat "$PID_FILE")
    if ! ps -p "$PID" > /dev/null 2>&1; then return 1; fi

    PARTICIPANT=$(ls -td "$DATA_DIR"/C[0-9][0-9][0-9] 2>/dev/null | head -1 | xargs basename)
    if [ -z "$PARTICIPANT" ]; then return 1; fi

    LATEST=$(ls -t "$DATA_DIR/$PARTICIPANT/" 2>/dev/null | head -1)
    if [ -z "$LATEST" ]; then return 1; fi

    DIR="$DATA_DIR/$PARTICIPANT/$LATEST"
    VIDEO="X"; AUDIO="X"; PPG="X"; GSR="X"
    ls "$DIR"/video_main* > /dev/null 2>&1 && VIDEO="OK"
    ls "$DIR"/audio* > /dev/null 2>&1 && AUDIO="OK"
    [ -f "$DIR/ppg.csv" ] && [ $(stat -c%s "$DIR/ppg.csv") -gt 100 ] && PPG="OK"
    [ -f "$DIR/gsr.csv" ] && [ $(stat -c%s "$DIR/gsr.csv") -gt 100 ] && GSR="OK"

    log "[$PARTICIPANT] $LATEST | v=$VIDEO a=$AUDIO ppg=$PPG gsr=$GSR"

    # 영상 없으면 — 카메라 재시작
    if [ "$VIDEO" = "X" ]; then
        log "[$PARTICIPANT] 영상 없음! 재시작"
        notify "영상 에러!" "$PARTICIPANT 영상 없음!"
        kill "$PID" 2>/dev/null
        sleep 3
        while [ $(ps aux | grep main.py | grep -v grep | wc -l) -gt 0 ]; do
            killall -9 python 2>/dev/null; sleep 2
        done
        # USB 리셋 안 함 (카메라 죽을 수 있음)
        sleep 3
        cd "$SENSING_DIR"
        PARTICIPANT_ID="$PARTICIPANT" setsid "$ENV_PY" -u "$MAIN_PY" > "$SENSING_DIR/logs/${PARTICIPANT}_recovery.log" 2>&1 &
        echo $! > "$PID_FILE"
        log "[$PARTICIPANT] 재시작 PID: $(cat $PID_FILE)"
        sleep 30
        LATEST2=$(ls -t "$DATA_DIR/$PARTICIPANT/" 2>/dev/null | head -1)
        VIDEO2="X"
        ls "$DATA_DIR/$PARTICIPANT/$LATEST2"/video_main* > /dev/null 2>&1 && VIDEO2="OK"
        if [ "$VIDEO2" = "OK" ]; then
            log "[$PARTICIPANT] 영상 복구 성공!"
            notify "영상 복구" "$PARTICIPANT 영상 복구됨!"
        else
            log "[$PARTICIPANT] 영상 복구 실패!"
            notify "영상 복구 실패" "$PARTICIPANT 확인 필요!"
        fi
    fi

    # 워치 끊기면 — 로그만 (리셋하면 카메라 영향 있음)
    if [ "$VIDEO" = "OK" ] && [ "$PPG" = "X" ] && [ "$GSR" = "X" ]; then
        log "[$PARTICIPANT] 워치 끊김 감지 (Flash 백업 중)"
    fi

    return 0
}

log "=== 모니터링 시작 ==="
sleep 120
check_sensing

while true; do
    sleep 300
    if [ ! -f "$PID_FILE" ]; then
        log "센싱 안 돌고 있음, 모니터링 종료"
        break
    fi
    check_sensing
done
