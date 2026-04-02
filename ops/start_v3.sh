#!/bin/bash
# K-MER 센싱 v3 시작 — 플래시 primary + 상태 폴링
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniforge3/etc/profile.d/conda.sh"
    conda activate sensing
fi
cd "$SCRIPT_DIR"

PARTICIPANT_ID="${1:-${PARTICIPANT_ID:-}}"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/start_$(date +%Y%m%d_%H%M%S).log"
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

# ═══ PRECHECK ═══
log "========== PRECHECK (v3) =========="
EXISTING=$(pgrep -f "python.*main.py" || true)
if [ -n "$EXISTING" ]; then
    log "WARNING: 이미 실행 중 (PID: $EXISTING). 먼저 stop하세요."
    exit 1
fi

DONGLE=$(ls /dev/ttyACM* 2>/dev/null | head -1)
if [ -z "$DONGLE" ]; then
    log "동글 안 잡힘. xhci 리셋..."
    sudo sh -c "echo $XHCI_PATH > /sys/bus/platform/drivers/tegra-xusb/unbind" 2>/dev/null || true
    sleep 3
    sudo sh -c "echo $XHCI_PATH > /sys/bus/platform/drivers/tegra-xusb/bind" 2>/dev/null || true
    sleep 8
    DONGLE=$(ls /dev/ttyACM* 2>/dev/null | head -1)
    [ -z "$DONGLE" ] && { log "FATAL: 동글 안 잡힘."; exit 1; }
fi
log "동글: $DONGLE"

AVAIL_GB=$(df / | tail -1 | awk '{printf "%d", $4/1024/1024}')
log "디스크: ${AVAIL_GB}GB"
[ "$AVAIL_GB" -lt 10 ] && { log "FATAL: 디스크 부족."; exit 1; }

if [ -z "$PARTICIPANT_ID" ]; then
    LATEST=$(ls -d data/C[0-9][0-9][0-9] 2>/dev/null | sort -V | tail -1 | xargs basename 2>/dev/null)
    if [ -n "$LATEST" ]; then
        NUM=${LATEST#C}; NUM=$((10#$NUM + 1))
        PARTICIPANT_ID=$(printf "C%03d" $NUM)
    else
        PARTICIPANT_ID="C001"
    fi
fi
log "참가자: $PARTICIPANT_ID"

# ═══ CLEANUP ═══
log "========== CLEANUP =========="
pkill -f "python.*main.py" 2>/dev/null || true
pkill -f "watch_standalone" 2>/dev/null || true
sleep 2

# ═══ START ═══
log "========== SENSING START (v3) =========="
sudo -n systemctl stop bluetooth 2>/dev/null || true
reset_dongle

# main.py (영상+오디오)
export PARTICIPANT_ID
nohup python3 -u core/main.py >> "$LOG_DIR/${PARTICIPANT_ID}_sensing.log" 2>&1 &
MAIN_PID=$!
echo "$MAIN_PID" > "$LOG_DIR/sensing.pid"
log "main.py 시작 (PID: $MAIN_PID)"

# watch_standalone_v3 (플래시 primary + 상태 폴링)
nohup python3 -u monitor/watch_standalone_v3.py "$PARTICIPANT_ID" \
    >> "$LOG_DIR/watch_standalone.log" 2>&1 &
WATCH_PID=$!
echo "$WATCH_PID" > "$LOG_DIR/watch.pid"
log "watch_standalone_v3 시작 (PID: $WATCH_PID)"

# ═══ VALIDATION ═══
log "========== VALIDATION (40s) =========="
sleep 40

DATA_DIR="data/$PARTICIPANT_ID"
V="X"; A="X"
LATEST_MIN=$(ls -td "$DATA_DIR"/20* 2>/dev/null | head -1)
if [ -n "$LATEST_MIN" ]; then
    ls "$LATEST_MIN"/video_main* >/dev/null 2>&1 && V="OK"
    ls "$LATEST_MIN"/audio* >/dev/null 2>&1 && A="OK"
fi

# 워치 상태 확인 (watch_status.json)
WATCH_STATE="확인 중"
if [ -f "$LOG_DIR/watch_status.json" ]; then
    FLASH=$(python3 -c "import json; d=json.load(open('$LOG_DIR/watch_status.json')); print('OK' if d.get('flash_logging') else 'NO')" 2>/dev/null || echo "?")
    BAT=$(python3 -c "import json; d=json.load(open('$LOG_DIR/watch_status.json')); print(d.get('battery_level', '?'))" 2>/dev/null || echo "?")
    WATCH_STATE="flash=$FLASH bat=${BAT}%"
fi

log "센서: v=$V a=$A 워치=$WATCH_STATE"

# ═══ BACKUP ═══
if ! pgrep -f "auto_backup" >/dev/null 2>&1; then
    if [ -f "$SCRIPT_DIR/ops/auto_backup.sh" ]; then
        nohup bash "$SCRIPT_DIR/ops/auto_backup.sh" "$PARTICIPANT_ID" >> "$LOG_DIR/backup.log" 2>&1 &
    fi
fi

log "========== READY (v3) =========="
log "v3: 워치 데이터는 내장 플래시에 저장됩니다."
log "BLE가 끊겨도 데이터는 안전합니다."
log "실험 후 크레들에 올려서 다운로드하세요."
