#!/bin/bash
# ============================================================
# daily_pipeline.sh — 매일 자동 센싱 파이프라인
# @reboot cron으로 실행, SSH/VPN 무관
# 3/23 레퍼런스: cron이 전부 처리, 수동 개입 없음
# ============================================================

BASE="/home/jetson/Desktop/sensing_code"
DATA="$BASE/data"
ENV_PY="/home/jetson/anaconda3/envs/sensing/bin/python"
EXT="/media/jetson/OneTouch"
TODAY=$(date +%Y%m%d)
LOG="$BASE/logs/daily_${TODAY}.log"
LOCKFILE="/tmp/daily_pipeline.lock"

# 중복 실행 방지
exec 200>$LOCKFILE
flock -n 200 || { echo "이미 실행 중"; exit 1; }

log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG"; }

wait_until() {
    local TARGET=$1
    local SECS=$(( $(date -d "$TARGET" +%s) - $(date +%s) ))
    if [ $SECS -gt 0 ]; then
        log "$TARGET 까지 ${SECS}초 대기"
        sleep $SECS
    fi
}

log "============================================"
log "=== 일일 파이프라인 시작 ($TODAY) ==="
log "============================================"

# ============================================================
# PHASE 1: 준비 (리부트 후)
# ============================================================
log "=== PHASE 1: 준비 ==="

# 내장 블루투스 끄기
sudo -n systemctl stop bluetooth 2>/dev/null

# 외장하드 마운트
if ! mountpoint -q "$EXT" 2>/dev/null; then
    log "외장하드 마운트 시도"
    sudo -n mount.exfat-fuse /dev/sda1 "$EXT" 2>/dev/null
    sleep 3
    if ! mountpoint -q "$EXT" 2>/dev/null; then
        log "WARN: 외장하드 마운트 실패 — 센싱은 계속, 삭제 건너뜀"
    else
        log "외장하드 마운트 OK"
    fi
fi

# xhci 풀 리셋
log "xhci 풀 리셋"
echo '3610000.usb' | sudo -n tee /sys/bus/platform/drivers/tegra-xusb/unbind 2>/dev/null
sleep 5
echo '3610000.usb' | sudo -n tee /sys/bus/platform/drivers/tegra-xusb/bind 2>/dev/null
sleep 10

# 장비 확인
CAM=$(lsusb | grep -c 8086)
DONGLE=$(ls /dev/ttyACM0 2>/dev/null && echo 1 || echo 0)
DISK_AVAIL=$(df / | tail -1 | awk '{print int($4/1024/1024)}')
log "장비: 카메라=${CAM}대, 동글=${DONGLE}, 디스크=${DISK_AVAIL}GB"

if [ "$CAM" -lt 2 ]; then
    log "ERROR: 카메라 부족 (${CAM}대), 계속 시도"
fi

# LT 활성화
log "LT 활성화"
cd "$BASE"
timeout 120 "$ENV_PY" -u activate_lt.py >> "$LOG" 2>&1
LT_RESULT=$?
if [ $LT_RESULT -ne 0 ]; then
    log "WARN: LT 활성화 실패 (code=$LT_RESULT)"
fi

# Flash 포맷 (이전 백업 확인 후)
if [ -f /tmp/flash_backup_verified ]; then
    log "Flash 포맷 (이전 백업 확인됨)"
    cd "$BASE"
    "$ENV_PY" -c "
from adi_study_watch import SDK
try:
    sdk = SDK('/dev/ttyACM0', check_version=False, check_existing_connection=False)
    fs = sdk.get_fs_application()
    fs.format()
    print('Flash 포맷 완료')
    sdk.disconnect()
except Exception as e:
    print(f'Flash 포맷 실패: {e}')
" >> "$LOG" 2>&1
    rm -f /tmp/flash_backup_verified
else
    log "Flash 포맷 건너뜀 (백업 미확인)"
fi

log "PHASE 1 완료"

# ============================================================
# PHASE 2: 테스트 센싱
# ============================================================
log "=== PHASE 2: 테스트 센싱 ==="
wait_until "09:30"

cd "$BASE"
PARTICIPANT_ID="PRETEST_${TODAY}" setsid "$ENV_PY" -u main.py > "$BASE/logs/PRETEST_${TODAY}.log" 2>&1 &
TEST_PID=$!
log "테스트 시작 PID=$TEST_PID"
sleep 60

# 모달리티 확인
TEST_DIR="$DATA/PRETEST_${TODAY}"
LATEST=$(ls -t "$TEST_DIR/" 2>/dev/null | head -1)
V="X"; A="X"; P="X"; G="X"; T="X"
if [ -n "$LATEST" ]; then
    ls "$TEST_DIR/$LATEST"/video_main* > /dev/null 2>&1 && V="OK"
    [ -f "$TEST_DIR/$LATEST/audio.wav.tmp" ] && A="OK"
    [ -f "$TEST_DIR/$LATEST/ppg.csv" ] && P="OK"
    [ -f "$TEST_DIR/$LATEST/gsr.csv" ] && G="OK"
    [ -f "$TEST_DIR/$LATEST/temp.csv" ] && T="OK"
fi
log "테스트 결과: v=$V a=$A ppg=$P gsr=$G temp=$T"

# 테스트 종료
kill $TEST_PID 2>/dev/null
sleep 3
while [ $(ps aux | grep main.py | grep -v grep | wc -l) -gt 0 ]; do
    killall -9 python 2>/dev/null
    sleep 2
done
pkill -f monitor_ble 2>/dev/null
rm -rf "$TEST_DIR"
log "테스트 정리 완료"

if [ "$V" != "OK" ] || [ "$A" != "OK" ]; then
    log "WARN: 테스트 영상/오디오 실패, 본 센싱은 시도"
fi

# ============================================================
# PHASE 3: 본 센싱 (09:50 ~ 18:10)
# ============================================================
log "=== PHASE 3: 본 센싱 ==="
wait_until "09:50"

# 참가자 ID (날짜 기반)
PARTICIPANT="S_${TODAY}"
log "본 센싱 시작: $PARTICIPANT"

cd "$BASE"
PARTICIPANT_ID="$PARTICIPANT" setsid "$ENV_PY" -u main.py > "$BASE/logs/${PARTICIPANT}.log" 2>&1 &
echo $! > /tmp/sensing_main.pid
log "센싱 PID=$(cat /tmp/sensing_main.pid)"

# monitor_ble2 시작 (setsid로 SSH 독립)
setsid bash "$BASE/monitor_ble2.sh" >> "$BASE/logs/ble_${TODAY}.log" 2>&1 &
echo $! > /tmp/monitor_ble2.pid
log "monitor_ble2 PID=$(cat /tmp/monitor_ble2.pid)"

# 35초 후 확인
sleep 35
LATEST=$(ls -t "$DATA/$PARTICIPANT/" 2>/dev/null | head -1)
V="X"; A="X"; P="X"; G="X"; T="X"
if [ -n "$LATEST" ]; then
    ls "$DATA/$PARTICIPANT/$LATEST"/video_main* > /dev/null 2>&1 && V="OK"
    [ -f "$DATA/$PARTICIPANT/$LATEST/audio.wav.tmp" ] && A="OK"
    [ -f "$DATA/$PARTICIPANT/$LATEST/ppg.csv" ] && P="OK"
    [ -f "$DATA/$PARTICIPANT/$LATEST/gsr.csv" ] && G="OK"
    [ -f "$DATA/$PARTICIPANT/$LATEST/temp.csv" ] && T="OK"
fi
log "본 센싱 확인: v=$V a=$A ppg=$P gsr=$G temp=$T"

# 18:10까지 대기
wait_until "18:10"

# ============================================================
# PHASE 4: 센싱 종료 + 후처리
# ============================================================
log "=== PHASE 4: 센싱 종료 ==="

# monitor_ble2 종료
kill $(cat /tmp/monitor_ble2.pid 2>/dev/null) 2>/dev/null
pkill -f monitor_ble 2>/dev/null

# 센싱 종료
kill $(cat /tmp/sensing_main.pid 2>/dev/null) 2>/dev/null
sleep 3

# 프로세스 완전 종료 확인
RETRY=0
while [ $(ps aux | grep main.py | grep -v grep | wc -l) -gt 0 ]; do
    killall -9 python 2>/dev/null
    sleep 2
    RETRY=$((RETRY+1))
    if [ $RETRY -gt 5 ]; then
        log "ERROR: main.py 종료 실패 (5회), 강제 진행"
        break
    fi
done
log "센싱 종료 완료 (retry=$RETRY)"

# 후처리
bash "$BASE/post_sensing.sh" "$PARTICIPANT" "$TODAY"

log "============================================"
log "=== 일일 파이프라인 완료 ($TODAY) ==="
log "============================================"
