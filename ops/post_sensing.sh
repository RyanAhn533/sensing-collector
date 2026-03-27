#!/bin/bash
# ============================================================
# post_sensing.sh — 센싱 종료 후 데이터 처리
# 외장하드 복사 → 검증 → 삭제 → Flash/RODE 백업
# 에러 시 삭제만 건너뜀 (데이터 보존 최우선)
# ============================================================

PARTICIPANT=$1
TODAY=$2
BASE="/home/jetson/Desktop/sensing_code"
DATA="$BASE/data"
ENV_PY="/home/jetson/anaconda3/envs/sensing/bin/python"
EXT="/media/jetson/OneTouch"
EXT_DATA="$EXT/sensing_data/$TODAY"
LOG="$BASE/logs/daily_${TODAY}.log"
SKIP_DELETE=0

log() { echo "[$(date '+%H:%M:%S')] [POST] $1" | tee -a "$LOG"; }

log "=== 후처리 시작 ($PARTICIPANT) ==="

# ============================================================
# STEP 1: 외장하드 복사 (~11분)
# ============================================================
log "STEP 1: 외장하드 복사"

if ! mountpoint -q "$EXT" 2>/dev/null; then
    log "외장하드 마운트 시도"
    sudo -n mount.exfat-fuse /dev/sda1 "$EXT" 2>/dev/null
    sleep 3
fi

if ! mountpoint -q "$EXT" 2>/dev/null; then
    log "ERROR: 외장하드 마운트 실패 — 삭제 건너뜀"
    SKIP_DELETE=1
fi

if [ $SKIP_DELETE -eq 0 ]; then
    sudo -n mkdir -p "$EXT_DATA"
    log "rsync 시작: $DATA/$PARTICIPANT → $EXT_DATA/$PARTICIPANT"
    sudo -n rsync -a "$DATA/$PARTICIPANT/" "$EXT_DATA/$PARTICIPANT/"
    COPY_RESULT=$?

    if [ $COPY_RESULT -ne 0 ]; then
        log "ERROR: rsync 실패 (code=$COPY_RESULT) — 삭제 건너뜀"
        SKIP_DELETE=1
    else
        log "rsync 완료"
    fi
fi

# ============================================================
# STEP 2: 검증
# ============================================================
if [ $SKIP_DELETE -eq 0 ]; then
    log "STEP 2: 복사 검증"
    bash "$BASE/verify_copy.sh" "$DATA/$PARTICIPANT" "$EXT_DATA/$PARTICIPANT" >> "$LOG" 2>&1
    VERIFY=$?

    if [ $VERIFY -ne 0 ]; then
        log "ERROR: 검증 실패 — 삭제 건너뜀"
        SKIP_DELETE=1
    else
        log "검증 통과"
    fi
fi

# ============================================================
# STEP 3: Jetson 디스크 삭제 (검증 통과 시에만)
# ============================================================
if [ $SKIP_DELETE -eq 0 ]; then
    log "STEP 3: Jetson 로컬 삭제"

    # 최종 확인: 외장하드 파일 수 > 0
    EXT_COUNT=$(find "$EXT_DATA/$PARTICIPANT" -type f 2>/dev/null | wc -l)
    if [ "$EXT_COUNT" -gt 0 ]; then
        rm -rf "$DATA/$PARTICIPANT"
        DISK_AVAIL=$(df / | tail -1 | awk '{print int($4/1024/1024)}')
        log "삭제 완료 (외장하드 파일: ${EXT_COUNT}개, 디스크 여유: ${DISK_AVAIL}GB)"
    else
        log "ERROR: 외장하드 파일 0개 — 삭제 중단"
    fi
else
    log "SKIP: 삭제 건너뜀 (에러 발생)"
fi

# ============================================================
# STEP 4: 워치 Flash 다운로드 + RODE TX 백업 (병렬, ~40분)
# ============================================================
log "STEP 4: Flash + RODE TX 백업"

# --- Flash 다운로드 (크래들 감지 루프) ---
(
    FLASH_RETRY=0
    while [ $FLASH_RETRY -lt 12 ]; do
        if lsusb | grep -q "1915:c00a"; then
            log "크래들 감지! Flash 다운로드 시작"
            cd "$BASE"
            "$ENV_PY" -u flash_download.py >> "$BASE/logs/flash_${TODAY}.log" 2>&1
            if [ $? -eq 0 ]; then
                log "Flash 다운로드 완료"
                # 외장하드에 복사
                if mountpoint -q "$EXT" 2>/dev/null; then
                    sudo -n mkdir -p "$EXT_DATA/flash"
                    sudo -n cp -r "$DATA/watch_flash_backup/"* "$EXT_DATA/flash/" 2>/dev/null
                    log "Flash → 외장하드 복사 완료"
                fi
                touch /tmp/flash_backup_verified
                break
            else
                log "Flash 다운로드 실패, 5분 후 재시도"
            fi
        else
            log "크래들 미감지 ($FLASH_RETRY/12), 5분 후 재시도"
        fi
        sleep 300
        FLASH_RETRY=$((FLASH_RETRY+1))
    done
    if [ $FLASH_RETRY -ge 12 ]; then
        log "WARN: Flash 다운로드 1시간 내 실패"
    fi
) &
FLASH_PID=$!

# --- RODE TX 백업 ---
(
    RODE_RETRY=0
    while [ $RODE_RETRY -lt 6 ]; do
        # RODE TX 스토리지 모드 확인
        if lsblk 2>/dev/null | grep -q "sd.*disk"; then
            RODE_DEV=$(lsblk -dpno NAME,SIZE | grep "sd" | head -1 | awk '{print $1}')
            if [ -n "$RODE_DEV" ] && [ "$RODE_DEV" != "/dev/sda" ]; then
                log "RODE TX 감지: $RODE_DEV"
                mkdir -p /tmp/rode_tx_mount
                sudo -n mount.exfat-fuse "$RODE_DEV" /tmp/rode_tx_mount 2>/dev/null || \
                sudo -n mount "$RODE_DEV" /tmp/rode_tx_mount 2>/dev/null

                if mountpoint -q /tmp/rode_tx_mount 2>/dev/null; then
                    RODE_DIR="$DATA/rode_tx_backup_${TODAY}"
                    mkdir -p "$RODE_DIR"
                    cp /tmp/rode_tx_mount/REC*.WAV "$RODE_DIR/" 2>/dev/null
                    RODE_COUNT=$(ls "$RODE_DIR/"*.WAV 2>/dev/null | wc -l)
                    log "RODE TX 백업 완료 (${RODE_COUNT}개 WAV)"

                    # 외장하드에도 복사
                    if mountpoint -q "$EXT" 2>/dev/null; then
                        sudo -n mkdir -p "$EXT_DATA/rode_tx"
                        sudo -n cp "$RODE_DIR/"*.WAV "$EXT_DATA/rode_tx/" 2>/dev/null
                        log "RODE TX → 외장하드 복사 완료"
                    fi
                    sudo -n umount /tmp/rode_tx_mount 2>/dev/null
                    break
                fi
            fi
        fi
        log "RODE TX 미감지 ($RODE_RETRY/6), 5분 후 재시도"
        sleep 300
        RODE_RETRY=$((RODE_RETRY+1))
    done
    if [ $RODE_RETRY -ge 6 ]; then
        log "WARN: RODE TX 30분 내 미감지"
    fi
) &
RODE_PID=$!

# 둘 다 대기
wait $FLASH_PID 2>/dev/null
wait $RODE_PID 2>/dev/null

# ============================================================
# STEP 5: 최종 상태 로그
# ============================================================
log "STEP 5: 최종 상태"
if mountpoint -q "$EXT" 2>/dev/null && [ -d "$EXT_DATA" ]; then
    FINAL_COUNT=$(find "$EXT_DATA" -type f 2>/dev/null | wc -l)
    FINAL_SIZE=$(du -sh "$EXT_DATA" 2>/dev/null | cut -f1)
    log "외장하드 최종: 파일 ${FINAL_COUNT}개, 용량 ${FINAL_SIZE}"
fi

DISK_AVAIL=$(df / | tail -1 | awk '{print int($4/1024/1024)}')
log "Jetson 디스크 여유: ${DISK_AVAIL}GB"

log "=== 후처리 완료 ==="
