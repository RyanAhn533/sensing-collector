#!/bin/bash
LOG="/home/jetson/Desktop/sensing_code/logs/precheck.log"
FAIL=0

log() { echo "$(date '+%H:%M:%S') $1" | tee -a "$LOG"; }
notify() {
    DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority \
    zenity --warning --title="$1" --text="$2" --timeout=30 2>/dev/null &
}

log "=== 사전 점검 ==="

AVAIL=$(df / | tail -1 | awk '{print $4}')
AVAIL_GB=$((AVAIL / 1024 / 1024))
if [ $AVAIL_GB -lt 20 ]; then
    log "FAIL: 디스크 ${AVAIL_GB}GB"; FAIL=1
else
    log "OK: 디스크 ${AVAIL_GB}GB"
fi

CAM=$(lsusb | grep -c "8086:0b07")
if [ $CAM -lt 2 ]; then
    log "FAIL: 카메라 ${CAM}대"; FAIL=1
else
    log "OK: 카메라 ${CAM}대"
fi

DONGLE=$(lsusb | grep -c "0456:2cfe")
if [ $DONGLE -lt 1 ]; then
    log "FAIL: 동글 없음"; FAIL=1
else
    log "OK: 동글"
fi

PYCOUNT=$(ps aux | grep main.py | grep -v grep | wc -l)
if [ $PYCOUNT -gt 0 ]; then
    log "WARNING: python ${PYCOUNT}개, 정리"
    killall -9 python 2>/dev/null; sleep 2
fi

if [ $FAIL -eq 0 ]; then
    log "=== 점검 통과 ==="
    notify "점검 통과" "디스크:${AVAIL_GB}GB 카메라:${CAM} 동글:OK"
else
    log "=== 점검 실패 ==="
    notify "점검 실패!" "디스크:${AVAIL_GB}GB 카메라:${CAM} 동글:${DONGLE}"
fi
