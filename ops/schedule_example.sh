#!/bin/bash
SENSING_DIR="/home/jetson/Desktop/sensing_code"
ENV_PY="/home/jetson/anaconda3/envs/sensing/bin/python"
LOG="$SENSING_DIR/logs/schedule_0327.log"

log() { echo "$(date +%H:%M:%S) $1" | tee -a "$LOG"; }

start_sensing() {
    PID=$1
    log "=== $PID 시작 ==="
    cd "$SENSING_DIR"
    PARTICIPANT_ID="$PID" setsid "$ENV_PY" -u main.py > "$SENSING_DIR/logs/${PID}.log" 2>&1 &
    echo $! > /tmp/sensing_main.pid
    log "$PID PID=$(cat /tmp/sensing_main.pid)"
    sleep 35
    LATEST=$(ls -t "$SENSING_DIR/data/$PID/" 2>/dev/null | head -1)
    V="X"; A="X"; P="X"; G="X"; T="X"
    ls "$SENSING_DIR/data/$PID/$LATEST"/video_main* > /dev/null 2>&1 && V="OK"
    [ -f "$SENSING_DIR/data/$PID/$LATEST/audio.wav.tmp" ] && A="OK"
    [ -f "$SENSING_DIR/data/$PID/$LATEST/ppg.csv" ] && P="OK"
    [ -f "$SENSING_DIR/data/$PID/$LATEST/gsr.csv" ] && G="OK"
    [ -f "$SENSING_DIR/data/$PID/$LATEST/temp.csv" ] && T="OK"
    log "$PID 확인: v=$V a=$A ppg=$P gsr=$G temp=$T"
    setsid bash "$SENSING_DIR/monitor_ble2.sh" > /dev/null 2>&1 &
    log "$PID monitor_ble2 시작 PID=$!"
}

stop_sensing() {
    log "=== 종료 ==="
    kill $(cat /tmp/sensing_main.pid 2>/dev/null) 2>/dev/null
    sleep 3
    while [ $(ps aux | grep main.py | grep -v grep | wc -l) -gt 0 ]; do
        kill -9 $(pgrep -f main.py) 2>/dev/null; sleep 2
    done
    pkill -f monitor_ble 2>/dev/null
    rm -f /tmp/sensing_main.pid
    REMAIN=$(ps aux | grep main.py | grep -v grep | wc -l)
    log "종료 완료 (잔여: $REMAIN)"
    if [ "$REMAIN" -gt 0 ]; then
        kill -9 $(pgrep -f main.py) 2>/dev/null
        sleep 2
        log "강제 종료 후 잔여: $(ps aux | grep main.py | grep -v grep | wc -l)"
    fi
}

wait_until() {
    TARGET=$1
    SECS=$(( $(date -d "$TARGET" +%s) - $(date +%s) ))
    if [ $SECS -gt 0 ]; then
        log "$TARGET 까지 ${SECS}초 대기"
        sleep $SECS
    fi
}

precheck() {
    log "=== 사전 점검 ==="
    sudo -n kill -9 $(pgrep -f flash_download) 2>/dev/null
    pkill -f monitor_sens 2>/dev/null

    CAM=$(lsusb | grep 8086 | wc -l)
    DONGLE=$(ls /dev/ttyACM0 2>/dev/null && echo 1 || echo 0)
    DISK=$(df / | tail -1 | awk '{print $4}')
    log "카메라: ${CAM}대, 동글: $DONGLE, 디스크: ${DISK}KB"

    if [ "$CAM" -lt 2 ] || [ "$DONGLE" = "0" ]; then
        log "장치 이상! 카메라=$CAM 동글=$DONGLE"
        return 1
    fi
    log "장치 OK"
    return 0
}

test_sensing() {
    log "=== 테스트 센싱 ==="
    cd "$SENSING_DIR"
    PARTICIPANT_ID="PRETEST" setsid "$ENV_PY" -u main.py > "$SENSING_DIR/logs/PRETEST.log" 2>&1 &
    echo $! > /tmp/sensing_main.pid
    sleep 40

    LATEST=$(ls -t "$SENSING_DIR/data/PRETEST/" 2>/dev/null | head -1)
    V="X"; A="X"; P="X"
    ls "$SENSING_DIR/data/PRETEST/$LATEST"/video_main* > /dev/null 2>&1 && V="OK"
    [ -f "$SENSING_DIR/data/PRETEST/$LATEST/audio.wav.tmp" ] && A="OK"
    [ -f "$SENSING_DIR/data/PRETEST/$LATEST/ppg.csv" ] && P="OK"
    log "테스트 결과: v=$V a=$A ppg=$P"

    kill $(cat /tmp/sensing_main.pid 2>/dev/null) 2>/dev/null
    sleep 3
    kill -9 $(pgrep -f main.py) 2>/dev/null
    pkill -f monitor_ble 2>/dev/null
    rm -f /tmp/sensing_main.pid
    sleep 2

    if [ "$V" = "OK" ] && [ "$A" = "OK" ]; then
        log "테스트 PASS"
        rm -rf "$SENSING_DIR/data/PRETEST"
        return 0
    else
        log "테스트 FAIL — 5분 후 재시도"
        rm -rf "$SENSING_DIR/data/PRETEST"
        return 1
    fi
}

log "=== 3/27 스케줄 시작 ==="

# 사전 점검
precheck

# 테스트
MAX_TRY=3
for i in $(seq 1 $MAX_TRY); do
    test_sensing && break
    if [ $i -lt $MAX_TRY ]; then
        log "재시도 $i/$MAX_TRY — 5분 대기"
        sleep 300
    else
        log "테스트 $MAX_TRY회 실패! 수동 확인 필요"
    fi
done

# 3/27(금): 10-12(2세션), 13-15(2세션), 17-18(1세션) = 5세션
PARTICIPANTS=("C038" "C039" "C040" "C041" "C042")
STARTS=("09:55" "10:55" "12:55" "13:55" "16:55")
STOPS=("10:50" "11:50" "13:50" "14:50" "17:50")

for i in $(seq 0 4); do
    P=${PARTICIPANTS[$i]}
    wait_until "${STARTS[$i]}"
    start_sensing "$P"
    wait_until "${STOPS[$i]}"
    stop_sensing
done

log "=== 3/27 스케줄 완료 ==="
