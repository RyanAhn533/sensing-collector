#!/bin/bash
# K-MER 자동 백업 — 센싱 중 백그라운드 실행
# 1시간마다 완료된 분 폴더를 백업 대상으로 복사
#
# 백업 우선순위:
#   1. 외장하드 (연결되어 있으면)
#   2. Google Cloud Storage (gsutil 있으면)
#   3. NAS (마운트되어 있으면)
#
# Usage: (start.sh에서 자동 실행)
#   nohup bash ops/auto_backup.sh C040 >> logs/backup.log 2>&1 &

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

PARTICIPANT="${1:-$(ls -td data/C[0-9][0-9][0-9] 2>/dev/null | head -1 | xargs basename 2>/dev/null)}"
DATA_DIR="$SCRIPT_DIR/data/$PARTICIPANT"
BACKUP_INTERVAL=3600  # 1시간마다
MIN_COMPLETE_AGE=120  # 2분 이상 된 폴더만 (현재 쓰는 중인 건 제외)

# 백업 대상 경로
EXTDISK=""  # 외장하드 자동 감지
GCS_BUCKET="${GCS_BUCKET:-}"  # gs://your-bucket/sensing/
NAS_PATH="${NAS_PATH:-}"  # /mnt/nas/sensing/ 또는 smb://...

LOG_PREFIX="[BACKUP $(date +%H:%M)]"

log() { echo "$LOG_PREFIX $*"; }

# ── 외장하드 자동 감지 ──
find_external_drive() {
    for mnt in /media/jetson/* /mnt/*; do
        if [ -d "$mnt" ] && mountpoint -q "$mnt" 2>/dev/null; then
            AVAIL=$(df "$mnt" | tail -1 | awk '{print $4}')
            if [ "$AVAIL" -gt 10485760 ]; then  # 10GB 이상 여유
                echo "$mnt"
                return 0
            fi
        fi
    done
    return 1
}

# ── 완료된 분 폴더 목록 ──
get_completed_folders() {
    local now=$(date +%s)
    for dir in "$DATA_DIR"/20*; do
        [ -d "$dir" ] || continue
        local mtime=$(stat -c%Y "$dir" 2>/dev/null || echo 0)
        local age=$(( now - mtime ))
        if [ "$age" -gt "$MIN_COMPLETE_AGE" ]; then
            echo "$dir"
        fi
    done
}

# ── 백업 실행 ──
backup_to_disk() {
    local src="$1"
    local dst_base="$2"
    local dst="$dst_base/$PARTICIPANT/$(basename $src)"

    if [ -d "$dst" ]; then
        # 이미 백업됨 — 파일 수 비교
        local src_count=$(ls "$src" 2>/dev/null | wc -l)
        local dst_count=$(ls "$dst" 2>/dev/null | wc -l)
        if [ "$src_count" -eq "$dst_count" ]; then
            return 0  # 이미 완료
        fi
    fi

    mkdir -p "$dst_base/$PARTICIPANT"
    cp -r "$src" "$dst_base/$PARTICIPANT/" 2>/dev/null

    # 검증
    local src_count=$(ls "$src" 2>/dev/null | wc -l)
    local dst_count=$(ls "$dst" 2>/dev/null | wc -l)
    if [ "$src_count" -eq "$dst_count" ]; then
        log "OK: $(basename $src) → $dst_base ($src_count files)"
        return 0
    else
        log "WARN: $(basename $src) 파일 수 불일치 (src=$src_count dst=$dst_count)"
        return 1
    fi
}

backup_to_gcs() {
    local src="$1"
    local dst="$GCS_BUCKET/$PARTICIPANT/$(basename $src)/"

    if ! command -v gsutil &>/dev/null; then
        return 1
    fi

    gsutil -m cp -r "$src" "$dst" 2>/dev/null
    if [ $? -eq 0 ]; then
        log "GCS OK: $(basename $src)"
        return 0
    fi
    return 1
}

backup_to_gdrive() {
    local src="$1"
    local dst="gdrive:KMER_Sensing_Backup/$PARTICIPANT/$(basename $src)"

    if ! command -v rclone &>/dev/null; then
        return 1
    fi

    # gdrive remote 있는지 확인
    if ! rclone listremotes 2>/dev/null | grep -q "gdrive:"; then
        return 1
    fi

    rclone copy "$src" "$dst" --quiet 2>/dev/null
    if [ $? -eq 0 ]; then
        log "GDrive OK: $(basename $src)"
        return 0
    fi
    return 1
}

# ── 메인 루프 ──
log "자동 백업 시작. 참가자: $PARTICIPANT"
log "간격: ${BACKUP_INTERVAL}초, 대상: $DATA_DIR"

while true; do
    # 백업 대상 확인
    if [ ! -d "$DATA_DIR" ]; then
        sleep 60
        continue
    fi

    FOLDERS=$(get_completed_folders)
    TOTAL=$(echo "$FOLDERS" | grep -c "20" 2>/dev/null || echo 0)
    BACKED=0
    FAILED=0

    if [ "$TOTAL" -gt 0 ]; then
        # 1순위: 외장하드
        EXTDISK=$(find_external_drive 2>/dev/null || true)

        for folder in $FOLDERS; do
            DONE=false

            # 1순위: 외장하드
            if [ -n "$EXTDISK" ] && ! $DONE; then
                backup_to_disk "$folder" "$EXTDISK/sensing_backup" && { BACKED=$((BACKED+1)); DONE=true; }
            fi

            # 2순위: Google Drive (rclone)
            if ! $DONE && command -v rclone &>/dev/null; then
                backup_to_gdrive "$folder" && { BACKED=$((BACKED+1)); DONE=true; }
            fi

            # 3순위: Google Cloud Storage
            if ! $DONE && [ -n "$GCS_BUCKET" ]; then
                backup_to_gcs "$folder" && { BACKED=$((BACKED+1)); DONE=true; }
            fi

            # 4순위: NAS
            if ! $DONE && [ -n "$NAS_PATH" ] && [ -d "$NAS_PATH" ]; then
                backup_to_disk "$folder" "$NAS_PATH" && { BACKED=$((BACKED+1)); DONE=true; }
            fi

            if ! $DONE; then
                FAILED=$((FAILED+1))
            fi
        done

        if [ "$BACKED" -gt 0 ] || [ "$FAILED" -gt 0 ]; then
            log "백업 완료: $BACKED OK, $FAILED FAIL (총 $TOTAL 폴더)"
        fi
    fi

    # 디스크 여유 경고
    AVAIL_GB=$(df / | tail -1 | awk '{printf "%d", $4/1024/1024}')
    if [ "$AVAIL_GB" -lt 20 ]; then
        log "WARNING: 디스크 ${AVAIL_GB}GB 남음! 백업 확인 후 정리 필요"
    fi

    sleep "$BACKUP_INTERVAL"
done
