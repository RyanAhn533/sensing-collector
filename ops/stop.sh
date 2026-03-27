#!/bin/bash
# K-MER 센싱 안전 종료
# Usage: ./stop.sh

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG="$LOG_DIR/stop_$(date +%Y%m%d_%H%M%S).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "========== SENSING STOP =========="

# 1. main.py 종료
PID_FILE="$LOG_DIR/sensing.pid"
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" >/dev/null 2>&1; then
        log "main.py 종료 중 (PID: $PID)..."
        kill "$PID" 2>/dev/null
        sleep 5
        if ps -p "$PID" >/dev/null 2>&1; then
            log "강제 종료..."
            kill -9 "$PID" 2>/dev/null
            sleep 2
        fi
    fi
    rm -f "$PID_FILE"
fi

# 2. 남은 python 프로세스 정리
REMAINING=$(pgrep -f "python.*main.py" || true)
if [ -n "$REMAINING" ]; then
    log "남은 프로세스 정리: $REMAINING"
    kill $REMAINING 2>/dev/null
    sleep 3
    kill -9 $REMAINING 2>/dev/null || true
fi

# 3. 모니터 종료
pkill -f "monitor_ble" 2>/dev/null || true

# 4. 프로세스 확인
STILL=$(pgrep -f "python.*main.py" || true)
if [ -n "$STILL" ]; then
    log "WARNING: 프로세스 아직 남아있음: $STILL"
else
    log "모든 프로세스 종료 완료"
fi

# 5. 즉석 검증 제안
log "========== POST-CHECK =========="
LATEST_SESSION=$(ls -td data/C* 2>/dev/null | head -1)
if [ -n "$LATEST_SESSION" ]; then
    MINUTES=$(ls -d "$LATEST_SESSION"/20* 2>/dev/null | wc -l)
    log "마지막 세션: $(basename $LATEST_SESSION), ${MINUTES}분 데이터"
    log "검증 실행: python3 validate/validate_session.py $LATEST_SESSION"
fi

log "센싱 종료 완료."
