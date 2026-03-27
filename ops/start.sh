#!/bin/bash
# K-MER 센싱 시작 — precheck → main.py → monitor 자동 실행
# Usage: ./start.sh [PARTICIPANT_ID]
#   ./start.sh C040
#   PARTICIPANT_ID=C040 ./start.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

PARTICIPANT_ID="${1:-${PARTICIPANT_ID:-}}"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/start_$(date +%Y%m%d_%H%M%S).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# ═══ 1. PRECHECK ═══
log "========== PRECHECK =========="

# 기존 센싱 프로세스 확인
EXISTING=$(pgrep -f "python.*main.py" || true)
if [ -n "$EXISTING" ]; then
    log "WARNING: 센싱 프로세스 이미 실행 중 (PID: $EXISTING)"
    log "먼저 stop.sh를 실행하세요."
    exit 1
fi

# USB 장치 확인
log "USB 장치 확인..."
DONGLE=$(ls /dev/ttyACM* 2>/dev/null | head -1)
VIDEO=$(ls /dev/video* 2>/dev/null | head -1)

if [ -z "$DONGLE" ]; then
    log "ERROR: 워치 동글 안 잡힘 (/dev/ttyACM* 없음)"
    log "xhci 리셋 시도..."
    sudo sh -c 'echo 3610000.usb > /sys/bus/platform/drivers/tegra-xusb/unbind' 2>/dev/null || true
    sleep 3
    sudo sh -c 'echo 3610000.usb > /sys/bus/platform/drivers/tegra-xusb/bind' 2>/dev/null || true
    sleep 5
    DONGLE=$(ls /dev/ttyACM* 2>/dev/null | head -1)
    if [ -z "$DONGLE" ]; then
        log "FATAL: 동글 여전히 안 잡힘. USB 물리적 확인 필요."
        exit 1
    fi
    log "xhci 리셋 후 동글 복구: $DONGLE"
fi
log "동글: $DONGLE"

if [ -z "$VIDEO" ]; then
    log "WARNING: 카메라 안 잡힘 (/dev/video* 없음). 영상 녹화 실패 가능."
fi

# 디스크 여유
AVAIL_GB=$(df / | tail -1 | awk '{printf "%d", $4/1024/1024}')
log "디스크 여유: ${AVAIL_GB}GB"
if [ "$AVAIL_GB" -lt 10 ]; then
    log "FATAL: 디스크 ${AVAIL_GB}GB < 10GB. 데이터 정리 필요."
    exit 1
fi

# 참가자 ID
if [ -z "$PARTICIPANT_ID" ]; then
    # 자동 순번
    LATEST=$(ls -d data/C[0-9][0-9][0-9] 2>/dev/null | sort -V | tail -1 | xargs basename 2>/dev/null)
    if [ -n "$LATEST" ]; then
        NUM=${LATEST#C}
        NUM=$((10#$NUM + 1))
        PARTICIPANT_ID=$(printf "C%03d" $NUM)
    else
        PARTICIPANT_ID="C001"
    fi
    log "자동 참가자ID: $PARTICIPANT_ID"
fi
log "참가자: $PARTICIPANT_ID"

# ═══ 2. 센싱 시작 ═══
log "========== SENSING START =========="

# 블루투스 끄기 (동글 간섭 방지)
sudo -n systemctl stop bluetooth 2>/dev/null || true

# 동글 리셋
DONGLE_PATH=$(find /sys/bus/usb/devices/ -maxdepth 2 -name idVendor -exec grep -l 0456 {} \; 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
if [ -n "$DONGLE_PATH" ]; then
    sudo sh -c "echo 0 > ${DONGLE_PATH}/authorized" 2>/dev/null || true
    sleep 2
    sudo sh -c "echo 1 > ${DONGLE_PATH}/authorized" 2>/dev/null || true
    sleep 5
    log "동글 리셋 완료"
fi

# main.py 실행
export PARTICIPANT_ID
cd "$SCRIPT_DIR"
nohup python3 -u core/main.py >> "$LOG_DIR/${PARTICIPANT_ID}_sensing.log" 2>&1 &
MAIN_PID=$!
echo "$MAIN_PID" > "$LOG_DIR/sensing.pid"
log "main.py 시작 (PID: $MAIN_PID)"

# ═══ 3. 센서 검증 (35초 대기) ═══
log "========== VALIDATION (35s wait) =========="
sleep 35

DATA_DIR="data/$PARTICIPANT_ID"
if [ ! -d "$DATA_DIR" ]; then
    log "ERROR: 데이터 폴더 생성 안됨: $DATA_DIR"
    kill $MAIN_PID 2>/dev/null
    exit 1
fi

LATEST_MIN=$(ls -td "$DATA_DIR"/20* 2>/dev/null | head -1)
if [ -z "$LATEST_MIN" ]; then
    log "ERROR: 1분 폴더 없음"
    kill $MAIN_PID 2>/dev/null
    exit 1
fi

V="X"; A="X"; P="X"; G="X"; T="X"
ls "$LATEST_MIN"/video_main* >/dev/null 2>&1 && V="OK"
ls "$LATEST_MIN"/audio* >/dev/null 2>&1 && A="OK"
ls "$LATEST_MIN"/ppg* >/dev/null 2>&1 && P="OK"
ls "$LATEST_MIN"/gsr* >/dev/null 2>&1 && G="OK"
ls "$LATEST_MIN"/temp* >/dev/null 2>&1 && T="OK"

log "센서 상태: v=$V a=$A ppg=$P gsr=$G temp=$T"

if [ "$V" = "X" ]; then
    log "CRITICAL: 영상 없음!"
fi
if [ "$P" = "X" ]; then
    log "WARNING: PPG 없음 (워치 연결 확인)"
fi

# ═══ 4. 모니터링 시작 ═══
log "========== MONITORING =========="

# monitor_ble2.sh 시작
if ! pgrep -f "monitor_ble" >/dev/null 2>&1; then
    nohup bash monitor/monitor_ble2.sh >> "$LOG_DIR/monitor_ble.log" 2>&1 &
    log "monitor_ble2.sh 시작 (PID: $!)"
fi

# ═══ 5. 대시보드 자동 시작 ═══
log "========== DASHBOARD =========="

# GUI 대시보드 (모니터 있을 때)
if [ -n "$DISPLAY" ] || [ -f /tmp/.X11-unix/X1 ]; then
    export DISPLAY="${DISPLAY:-:1}"
    nohup python3 -u monitor/dashboard_gui.py --participant "$PARTICIPANT_ID" >> "$LOG_DIR/dashboard.log" 2>&1 &
    log "GUI 대시보드 시작 (PID: $!)"
else
    # SSH 등 모니터 없으면 터미널 대시보드 안내
    log "GUI 없음. 터미널 대시보드: python3 monitor/dashboard.py"
fi

# ═══ 6. 자동 백업 스케줄 ═══
if ! pgrep -f "auto_backup" >/dev/null 2>&1; then
    if [ -f "$SCRIPT_DIR/ops/auto_backup.sh" ]; then
        nohup bash "$SCRIPT_DIR/ops/auto_backup.sh" "$PARTICIPANT_ID" >> "$LOG_DIR/backup.log" 2>&1 &
        log "자동 백업 시작 (PID: $!)"
    fi
fi

log "========== READY =========="
log "센싱 시작 완료. $PARTICIPANT_ID"
log "v=$V a=$A ppg=$P gsr=$G temp=$T"
log "로그: $LOG_DIR/${PARTICIPANT_ID}_sensing.log"
log ""
log "자동 실행 중:"
log "  - 센싱 (main.py)"
log "  - BLE 모니터 (monitor_ble2.sh)"
log "  - 대시보드 (dashboard_gui.py)"
log "  - 자동 백업 (auto_backup.sh)"
log ""
log "모두 자동입니다. 건드리지 마세요."
