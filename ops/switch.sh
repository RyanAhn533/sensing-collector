#!/bin/bash
SENSING_DIR="/home/jetson/Desktop/sensing_code"
ENV_PY="/home/jetson/anaconda3/envs/sensing/bin/python"
MAIN_PY="$SENSING_DIR/main.py"
DATA_DIR="$SENSING_DIR/data"
PID_FILE="/tmp/sensing_main.pid"
LOG="$SENSING_DIR/logs/switch.log"

log() { echo "$(date +%H:%M:%S) $1" | tee -a "$LOG"; }
notify() { DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority zenity --info --title="$1" --text="$2" --timeout=10 2>/dev/null & }

# 1. 이전 센싱 완전 종료
log "=== 전환 시작 ==="
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    log "이전 PID: $PID"
    kill "$PID" 2>/dev/null
    sleep 3
fi
RETRY=0
while [ $(ps aux | grep "main.py" | grep -v grep | wc -l) -gt 0 ]; do
    RETRY=$((RETRY+1))
    log "프로세스 남아있음, 강제 종료 $RETRY"
    killall -9 python 2>/dev/null
    sleep 2
    [ $RETRY -ge 5 ] && log "ERROR: 종료 실패" && notify "에러" "프로세스 종료 실패!" && exit 1
done
log "프로세스 완전 종료"
rm -f "$PID_FILE"

# 2. USB 리셋 + 충분한 대기 + health 체크
log "USB 리셋..."
# 동글만 리셋 (카메라 Bus 02 영향 없음)
DONGLE_PATH=$(find /sys/bus/usb/devices/ -maxdepth 2 -name idVendor -exec grep -l 0456 {} \; 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
if [ -n "$DONGLE_PATH" ]; then
    sudo -n sh -c "echo 0 > ${DONGLE_PATH}/authorized" 2>/dev/null
    sleep 2
    sudo -n sh -c "echo 1 > ${DONGLE_PATH}/authorized" 2>/dev/null
    sleep 5
else
    sleep 3
fi

# ttyACM 확인 (최대 3회)
for i in 1 2 3; do
    if ls /dev/ttyACM* > /dev/null 2>&1; then break; fi
    log "ttyACM 없음, 대기 $i/3..."
    sleep 5
done

# USB health 체크
HEALTH=$($ENV_PY -c "
import usb1
ctx = usb1.USBContext()
for d in ctx.getDeviceList(skip_on_error=True):
    if d.getVendorID() == 0x0456:
        try: d.getSerialNumber(); print(OK)
        except: print(BAD)
        break
" 2>/dev/null)
log "USB health: $HEALTH"
if [ "$HEALTH" != "OK" ]; then
    log "USB 재리셋..."
    DONGLE_PATH=$(find /sys/bus/usb/devices/ -maxdepth 2 -name idVendor -exec grep -l 0456 {} \; 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
    if [ -n "$DONGLE_PATH" ]; then
        sudo -n sh -c "echo 0 > ${DONGLE_PATH}/authorized" 2>/dev/null
        sleep 2
        sudo -n sh -c "echo 1 > ${DONGLE_PATH}/authorized" 2>/dev/null
        sleep 5
    fi
fi
log "USB: $(ls /dev/ttyACM* 2>/dev/null)"

# 3. 디스크
AVAIL_GB=$(df / | tail -1 | awk '{printf "%d", $4/1024/1024}')
AVAIL_GB=$(df / | tail -1 | awk '{printf "%d", $4/1024/1024}')
AVAIL_GB=$(df / | tail -1 | awk '{printf "%d", $4/1024/1024}')

# 4. 참가자 번호
LAST=$(ls -d "$DATA_DIR"/C[0-9][0-9][0-9] 2>/dev/null | sort | tail -1 | xargs basename 2>/dev/null)
if [ -z "$LAST" ]; then NEXT="C001"
else NUM=$(echo "$LAST" | sed s/C//); NUM=$((10#$NUM + 1)); NEXT=$(printf "C%03d" $NUM); fi
log "참가자: $NEXT"

# 5. 센싱 시작
cd "$SENSING_DIR"
PARTICIPANT_ID="$NEXT" setsid "$ENV_PY" -u "$MAIN_PY" > "$SENSING_DIR/logs/${NEXT}_run.log" 2>&1 &
echo $! > "$PID_FILE"
log "시작 PID: $(cat $PID_FILE)"

# 6. 30초 후 영상 검증 (최대 3회 재시도)
for ATTEMPT in 1 2 3; do
    sleep 30
    LATEST=$(ls -t "$DATA_DIR/$NEXT/" 2>/dev/null | head -1)
    DIR="$DATA_DIR/$NEXT/$LATEST"
    VIDEO="X"
    ls "$DIR"/video_main* > /dev/null 2>&1 && VIDEO="OK"
    
    if [ "$VIDEO" = "OK" ]; then
        AUDIO="X"; PPG="X"; GSR="X"
        ls "$DIR"/audio* > /dev/null 2>&1 && AUDIO="OK"
        [ -f "$DIR/ppg.csv" ] && PPG="OK"
        [ -f "$DIR/gsr.csv" ] && GSR="OK"
        log "검증 OK ($ATTEMPT): v=$VIDEO a=$AUDIO ppg=$PPG gsr=$GSR"
        notify "센싱 시작" "$NEXT 정상\nv=$VIDEO a=$AUDIO ppg=$PPG gsr=$GSR"
        break
    else
        log "영상 없음 (시도 $ATTEMPT/3), 재시작..."
        kill $(cat "$PID_FILE") 2>/dev/null
        killall -9 python 2>/dev/null
        sleep 3
        sudo -n sh -c "echo 3610000.usb > /sys/bus/platform/drivers/tegra-xusb/unbind" 2>/dev/null
        sleep 3
        sudo -n sh -c "echo 3610000.usb > /sys/bus/platform/drivers/tegra-xusb/bind" 2>/dev/null
        sleep 10
        PARTICIPANT_ID="$NEXT" setsid "$ENV_PY" -u "$MAIN_PY" > "$SENSING_DIR/logs/${NEXT}_run${ATTEMPT}.log" 2>&1 &
        echo $! > "$PID_FILE"
        if [ $ATTEMPT -eq 3 ]; then
            notify "영상 에러!" "$NEXT 영상 3회 실패!"
            log "=== ERROR: $NEXT 영상 3회 실패 ==="
        fi
    fi
done
log "=== $NEXT 전환 완료 ==="

# 7. 모니터링
pkill -f monitor_sensing.sh 2>/dev/null
setsid bash "$SENSING_DIR/monitor_sensing.sh" >> "$SENSING_DIR/logs/monitor.log" 2>&1 &
log "모니터링 PID: $!"
