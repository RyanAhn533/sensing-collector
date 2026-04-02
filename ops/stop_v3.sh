#!/bin/bash
# K-MER 센싱 v3 안전 종료
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG="$LOG_DIR/stop_$(date +%Y%m%d_%H%M%S).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "========== SENSING STOP (v3) =========="

# watch_standalone 먼저 (SIGTERM → 플래시 로깅 정리)
WATCH_PID_FILE="$LOG_DIR/watch.pid"
if [ -f "$WATCH_PID_FILE" ]; then
    WPID=$(cat "$WATCH_PID_FILE")
    if ps -p "$WPID" >/dev/null 2>&1; then
        log "watch_standalone 종료 중 (PID: $WPID)..."
        kill "$WPID" 2>/dev/null
        sleep 5
        ps -p "$WPID" >/dev/null 2>&1 && kill -9 "$WPID" 2>/dev/null
    fi
    rm -f "$WATCH_PID_FILE"
fi

# main.py
PID_FILE="$LOG_DIR/sensing.pid"
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" >/dev/null 2>&1; then
        log "main.py 종료 중 (PID: $PID)..."
        kill "$PID" 2>/dev/null
        sleep 5
        ps -p "$PID" >/dev/null 2>&1 && kill -9 "$PID" 2>/dev/null
    fi
    rm -f "$PID_FILE"
fi

# 나머지 정리
pkill -f "python.*main.py" 2>/dev/null || true
pkill -f "watch_standalone" 2>/dev/null || true
sleep 2

STILL=$(pgrep -f "python.*(main\.py|watch_standalone)" || true)
[ -n "$STILL" ] && kill -9 $STILL 2>/dev/null || true

log "모든 프로세스 종료 완료"

# 워치 상태 파일 업데이트
echo '{"state": "종료됨", "flash_logging": false, "ble_connected": false, "battery_level": -1}' \
    > "$LOG_DIR/watch_status.json"

# 세션 요약
log "========== POST-CHECK =========="
LATEST=$(ls -td "$SCRIPT_DIR/data"/C* 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
    MINS=$(ls -d "$LATEST"/20* 2>/dev/null | wc -l)
    log "마지막 세션: $(basename $LATEST), ${MINS}분"
fi

log ""
log "워치 데이터는 워치 내장 메모리에 있습니다."
log "크레들에 올려서 '데이터 다운로드' 버튼을 누르세요."
log "센싱 v3 종료 완료."
