#!/bin/bash
# K-MER 센싱 시작 — precheck → main.py → monitor → dashboard 자동 실행
# Usage: ./start.sh [PARTICIPANT_ID]
#   ./start.sh C040
#   PARTICIPANT_ID=C040 ./start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── conda 환경 활성화 ──
if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniforge3/etc/profile.d/conda.sh"
    conda activate sensing
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
    conda activate sensing
fi

cd "$SCRIPT_DIR"

PARTICIPANT_ID="${1:-${PARTICIPANT_ID:-}}"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/start_$(date +%Y%m%d_%H%M%S).log"

# xhci 경로 (config.json에서 읽기, 실패 시 기본값)
XHCI_PATH=$(python3 -c "import json; print(json.load(open('config.json'))['jetson']['xhci_path'])" 2>/dev/null || echo "a80aa10000.usb")

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

find_dongle_sysfs() {
    for d in /sys/bus/usb/devices/*/idVendor; do
        v=$(cat "$d" 2>/dev/null)
        [ "$v" = "0456" ] && echo "$(dirname "$d")" && return
    done
}

reset_dongle() {
    DPATH=$(find_dongle_sysfs)
    if [ -n "$DPATH" ]; then
        sudo -n sh -c "echo 0 > ${DPATH}/authorized" 2>/dev/null || true
        sleep 2
        sudo -n sh -c "echo 1 > ${DPATH}/authorized" 2>/dev/null || true
        sleep 5
        log "동글 리셋: $DPATH"
    fi
}

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

if [ -z "$DONGLE" ]; then
    log "동글 안 잡힘. xhci 리셋..."
    sudo sh -c "echo $XHCI_PATH > /sys/bus/platform/drivers/tegra-xusb/unbind" 2>/dev/null || true
    sleep 3
    sudo sh -c "echo $XHCI_PATH > /sys/bus/platform/drivers/tegra-xusb/bind" 2>/dev/null || true
    sleep 8
    DONGLE=$(ls /dev/ttyACM* 2>/dev/null | head -1)
    if [ -z "$DONGLE" ]; then
        log "FATAL: 동글 여전히 안 잡힘."
        exit 1
    fi
    log "xhci 리셋 후 동글 복구: $DONGLE"
fi
log "동글: $DONGLE"

# 디스크 여유
AVAIL_GB=$(df / | tail -1 | awk '{printf "%d", $4/1024/1024}')
log "디스크 여유: ${AVAIL_GB}GB"
if [ "$AVAIL_GB" -lt 10 ]; then
    log "FATAL: 디스크 ${AVAIL_GB}GB < 10GB."
    exit 1
fi

# 참가자 ID
if [ -z "$PARTICIPANT_ID" ]; then
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

# ═══ 2. 기존 프로세스 정리 ═══
log "========== CLEANUP =========="
pkill -f "python.*main.py" 2>/dev/null || true
pkill -f "watch_standalone" 2>/dev/null || true
pkill -f "monitor_ble" 2>/dev/null || true
pkill -f "dashboard_gui" 2>/dev/null || true
sleep 2
log "기존 프로세스 정리 완료"

# ═══ 3. 센싱 시작 ═══
log "========== SENSING START =========="

# 블루투스 끄기 (동글 간섭 방지)
sudo -n systemctl stop bluetooth 2>/dev/null || true

# 동글 리셋 (깨끗한 상태)
reset_dongle

# main.py 실행 (영상+오디오만, 워치 없음)
export PARTICIPANT_ID
nohup python3 -u core/main.py >> "$LOG_DIR/${PARTICIPANT_ID}_sensing.log" 2>&1 &
MAIN_PID=$!
echo "$MAIN_PID" > "$LOG_DIR/sensing.pid"
log "main.py 시작 (PID: $MAIN_PID) — 영상+오디오"

# watch_standalone 실행 (워치 전담, 자동 재연결)
nohup python3 -u monitor/watch_standalone.py "$PARTICIPANT_ID" >> "$LOG_DIR/watch_standalone.log" 2>&1 &
WATCH_PID=$!
echo "$WATCH_PID" > "$LOG_DIR/watch.pid"
log "watch_standalone 시작 (PID: $WATCH_PID) — 워치 전담"

# ═══ 4. 센서 검증 (35초 대기) ═══
log "========== VALIDATION (35s wait) =========="
sleep 35

DATA_DIR="data/$PARTICIPANT_ID"
if [ ! -d "$DATA_DIR" ]; then
    log "ERROR: 데이터 폴더 생성 안됨: $DATA_DIR"
    kill $MAIN_PID 2>/dev/null
    exit 1
fi

LATEST_MIN=$(ls -td "$DATA_DIR"/20* 2>/dev/null | head -1)
V="X"; A="X"; P="X"; G="X"; T="X"
if [ -n "$LATEST_MIN" ]; then
    ls "$LATEST_MIN"/video_main* >/dev/null 2>&1 && V="OK"
    ls "$LATEST_MIN"/audio* >/dev/null 2>&1 && A="OK"
    ls "$LATEST_MIN"/ppg* >/dev/null 2>&1 && P="OK"
    ls "$LATEST_MIN"/gsr* >/dev/null 2>&1 && G="OK"
    ls "$LATEST_MIN"/temp* >/dev/null 2>&1 && T="OK"
fi

log "센서 상태: v=$V a=$A ppg=$P gsr=$G temp=$T"

# ═══ 4. 모니터링 시작 ═══
log "========== MONITORING =========="

if ! pgrep -f "monitor_ble" >/dev/null 2>&1; then
    nohup bash monitor/monitor_ble2.sh >> "$LOG_DIR/monitor_ble.log" 2>&1 &
    log "monitor_ble2.sh 시작 (PID: $!)"
fi

# ═══ 5. 대시보드 ═══
log "========== DASHBOARD =========="

# SSH든 로컬이든 DISPLAY=:1로 Jetson 모니터에 띄움
export DISPLAY=:1
nohup python3 -u monitor/dashboard_gui.py --participant "$PARTICIPANT_ID" >> "$LOG_DIR/dashboard.log" 2>&1 &
log "대시보드 시작 (PID: $!)"

# ═══ 6. 자동 백업 ═══
if ! pgrep -f "auto_backup" >/dev/null 2>&1; then
    if [ -f "$SCRIPT_DIR/ops/auto_backup.sh" ]; then
        nohup bash "$SCRIPT_DIR/ops/auto_backup.sh" "$PARTICIPANT_ID" >> "$LOG_DIR/backup.log" 2>&1 &
        log "자동 백업 시작 (PID: $!)"
    fi
fi

log "========== READY =========="
log "센싱 시작 완료. $PARTICIPANT_ID"
log "v=$V a=$A ppg=$P gsr=$G temp=$T"
log ""
log "자동 실행 중:"
log "  - main.py (영상+오디오)"
log "  - monitor_ble2.sh (워치 끊기면 자동 재시작)"
log "  - dashboard_gui.py (DISPLAY=:1)"
log "  - auto_backup.sh"
log ""
log "모두 자동입니다. 건드리지 마세요."
