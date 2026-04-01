# K-MER Sensing Collector

**차량 시뮬레이터 피실험자 멀티모달 데이터 수집 시스템** (v2.1, 2026-04-01)

Jetson Orin NX에서 운전 시뮬레이션 실험 중 피실험자의 영상, 음성, 생체신호를 동시 수집.
자동 모니터링 + 워치 자동 재연결 + 백업 + 검증까지 원커맨드 처리.

---

## 한줄 요약

> 바탕화면 **센싱시작.sh** 더블클릭 → 초록 버튼 → 끝. 워치 끊기면 자동 재연결.

---

## 실험자가 알아야 할 것 (이것만 읽으면 됨)

### 센싱 시작
1. **워치**: 충전 크래들에서 빼기 → **옆면 Navigation 버튼 1초 꾹** → 화면 밝아지면 OK → 피실험자 손목에 채우기
2. **마이크**: RODE TX 전원 켜기 → 피실험자 옷에 클립
3. **시작**: 바탕화면 `센싱시작.sh` 더블클릭 → "터미널에서 실행" → K-MER 창에서 초록 **[센싱 시작]** 클릭
4. **확인**: 40초 후 LED 전부 초록이면 실험 진행

### 센싱 종료
- K-MER 창에서 빨간 **[센싱 종료]** 클릭
- 또는 저녁 7시에 자동 종료

### 참가자 전환
- [센싱 종료] → 워치 교체 (옆면 버튼 눌러 깨우기) → [센싱 시작]

### 문제 생겼을 때
1. **2분 기다리기** — 자동 복구됨
2. K-MER 창에서 보라색 **[문제해결]** 버튼 → 단계별 안내
3. 해결 안 되면 **준영이에게 연락** (원격 해결 가능)

### 절대 하지 말 것
- USB 케이블 건드리기
- 터미널에서 Ctrl+C
- 컴퓨터 끄기/재시작
- 프로그램 설치

---

## Jetson 바탕화면 구조

```
~/바탕화면/
├── 센싱시작.sh              ← 이것만 더블클릭하면 됨
└── 20260401_세팅완료/       ← 인수인계 폴더
    ├── 해볼것_메모.txt      ← 전체 사용법 요약
    ├── 사용매뉴얼.sh        ← HTML 매뉴얼 열기
    ├── 센싱종료.sh          ← 수동 종료
    ├── 데이터검증.sh        ← 최근 데이터 검증
    └── _이전문서/           ← 구버전 문서 보관
```

---

## 전체 시스템 구조 (v2.1)

```
start.sh (원커맨드)
  ├── 1. Precheck (USB/디스크 확인)
  ├── 2. Cleanup (기존 프로세스 정리)
  ├── 3. main.py (영상+오디오만)          ← 동글 안 씀
  ├── 4. watch_standalone.py (워치 전담)  ← 동글 단독 소유, 자동 재연결
  ├── 5. Validation (35초 후 v/a/ppg/gsr/temp 검증)
  ├── 6. monitor_ble2.sh (워치 감시, 2분마다 체크)
  ├── 7. dashboard_gui.py (모니터에 LED+그래프 표시)
  └── 8. auto_backup.sh (1시간마다 외장하드/GDrive 백업)
```

### 왜 main.py와 watch를 분리했나

| 구조 | 문제 | 결과 |
|------|------|------|
| v2.0: main.py 안에 watch 스레드 | 동글을 main.py + watch_standalone이 경쟁 | LIBUSB_ERROR_BUSY 하루 12건 |
| **v2.1: watch_standalone 별도 프로세스** | **동글 소유자 1개** | **BUSY 원천 차단** |

---

## 수집 데이터

| 센서 | 데이터 | 파일 | 1분당 |
|------|--------|------|-------|
| RealSense D435 (정면) | 1920x1080 H.264 30fps | video_main.mp4 | ~35MB |
| RealSense D435 (측면) | 1920x1080 H.264 30fps | video_sub.mp4 | ~33MB |
| RODE Wireless GO II | 48kHz WAV | audio.wav | ~5.5MB |
| ADI Watch (PPG) | 심박 100Hz | ppg.csv | ~32KB |
| ADI Watch (EDA) | 피부전도 30Hz | gsr.csv | ~12KB |
| ADI Watch (Temp) | 피부온도 1Hz | temp.csv | ~0.8KB |

**1시간 = ~4.5GB, 하루 8시간 = ~36GB**

---

## 프로젝트 디렉토리

```
sensing-collector/
│
├── launcher.py                 # GUI 런처 (시작/종료/문제해결 위자드/대시보드)
├── 센싱시작.sh                  # 바탕화면용 실행 스크립트
├── 센싱시작.desktop             # 바탕화면 아이콘
├── config.json                 # 장비 설정 (시리얼, xhci 경로, 임계값)
├── CLAUDE.md                   # Claude Code 운영 가이드 (문제 시 자동 진단용)
│
├── core/                       # 센서 드라이버
│   ├── main.py                 # 오케스트레이터 — 영상+오디오만 (워치 없음)
│   ├── watch.py                # ADI Watch BLE 드라이버 (SDK 패치 포함)
│   ├── realsense.py            # RealSense D435 GStreamer H.264 인코딩
│   ├── rode.py                 # RODE Wireless GO II WAV 녹음
│   └── rt_pub.py               # ZeroMQ 실시간 퍼블리셔 (미사용)
│
├── monitor/                    # 모니터링 + 워치 관리
│   ├── watch_standalone.py     # 워치 전담 프로세스 (핵심!)
│   │                           #   - 동글 단독 소유
│   │                           #   - BLE 120초 타임아웃 → 동글 리셋 → 자동 재연결
│   │                           #   - 무한 재시도 (MAX_RETRIES=999)
│   ├── monitor_ble2.sh         # 워치 감시 v3 (2분마다 ppg.csv 체크)
│   │                           #   - 150초 이상 데이터 없으면 watch_standalone 재시작
│   │                           #   - watch_standalone과 이중 안전망
│   ├── dashboard_gui.py        # GUI 대시보드 (OpenCV)
│   │                           #   - 카메라 프리뷰 + PPG/GSR/Temp 그래프 + 오디오 파형
│   ├── dashboard.py            # 터미널 대시보드 (SSH용)
│   ├── monitor_sensing.sh      # 영상 감시 (5분마다)
│   ├── monitor.py              # 데이터 무결성 모니터
│   └── watchdog.sh             # 프로세스 워치독
│
├── ops/                        # 운영 스크립트
│   ├── start.sh                # 원커맨드 시작 (conda 자동, xhci 동적, 프로세스 정리)
│   ├── stop.sh                 # 안전 종료
│   ├── switch.sh               # 참가자 전환
│   ├── precheck.sh             # USB/디스크 사전 체크
│   ├── auto_backup.sh          # 자동 백업 (외장하드/GDrive)
│   ├── setup_gdrive.sh         # Google Drive 연결 (rclone, 최초 1회)
│   ├── setup_cron.sh           # 19:00 자동 종료/검증/백업 스케줄
│   ├── sync_gdrive_to_nas.py   # GDrive→NAS 전송 (2회 검증 후 Drive 삭제)
│   ├── daily_pipeline.sh       # 하루 전체 자동화
│   ├── post_sensing.sh         # 종료 후 처리
│   ├── verify_copy.sh          # 백업 검증
│   ├── schedule_example.sh     # 스케줄 예시
│   └── auto_sensing.py         # 자동 인원 감지
│
├── recovery/                   # 워치 복구 도구
│   ├── activate_lt.py          # LT 자율로깅 활성화 (SDK disconnect+동글 리셋 포함)
│   ├── flash_download.py       # 워치 Flash 데이터 다운로드
│   ├── recover_watch_data.py   # Flash → CSV 복원
│   ├── enable_lt_only.py       # LT Only 모드
│   ├── setup_lt_full.py        # LT 전체 세팅
│   ├── setup_lt_logging.py     # LT 로깅 설정
│   └── test_eda_now.py         # EDA 단독 테스트
│
├── validate/                   # 데이터 검증
│   └── validate_session.py     # 세션 검증 (PASS / PASS_WITH_WARNINGS / FAIL)
│
├── dcb_cfg/                    # 워치 센서 설정 파일 (DCB)
│   ├── DVT1_MV_UC2_ADPD_dcb.dcfg
│   ├── DVT2_MV_UC2_ADPD_dcb.dcfg
│   └── lt_app_dcb.lcfg
│
├── tests/                      # 테스트 + 에뮬레이터
│   ├── watch_emulator.py       # ADI Watch 디지털 트윈
│   │                           #   - PPG 100Hz 심박 파형 생성
│   │                           #   - EDA 30Hz 피부전도 생성
│   │                           #   - Temp 1Hz 피부온도 생성
│   │                           #   - BLE 연결/끊김 시뮬 (20~40분 주기)
│   │                           #   - LIBUSB_ERROR_BUSY 재현
│   │                           #   - WATCH_EMULATOR=1 로 활성화
│   └── test_launcher_sim.py    # launcher GUI 테스트 (센서 목업)
│
├── docs/                       # 문서
│   ├── 00_USER_MANUAL.html     # HTML 사용 매뉴얼 (비개발자용)
│   ├── 01_QUICK_START.md       # 5분 시작 가이드
│   ├── 02_MONITORING.md        # 실험 중 확인법
│   ├── 03_TROUBLESHOOTING.md   # 문제 해결 (워치 BUSY 해결법 포함)
│   ├── 04_DATA_VALIDATION.md   # 데이터 검증
│   ├── 05_HARDWARE_SETUP.md    # 장비 연결
│   ├── 06_DASHBOARD.md         # 대시보드 사용법
│   ├── 07_WATCH_ISSUE_ANALYSIS_20260401.md  # 워치 문제 근본 분석
│   └── EVAL-HCRWATCH4Z_UserGuide.pdf       # ADI Watch 하드웨어 매뉴얼
│
└── data/                       # 수집 데이터 (git 미포함)
    └── C001/
        └── 20260401_1400/
            ├── video_main.mp4
            ├── video_sub.mp4
            ├── audio.wav
            ├── ppg.csv
            ├── gsr.csv
            └── temp.csv
```

---

## 워치 (ADI EVAL-HCRWATCH4Z)

### 깨우기
- **옆면 Navigation 버튼 1초 꾹 누르기** (화면 터치 아님!)
- 화면이 밝아지면 BLE 광고 시작 → 자동 연결

### 리셋
- Navigation 버튼 3초 → 소프트 리셋
- Action + Navigation 동시 3초 → 부트로더 진입

### 데이터 보호 (4중)

| Layer | 방식 | 설명 |
|-------|------|------|
| 1 | BLE 실시간 | watch_standalone → ppg/gsr/temp.csv 즉시 저장 |
| 2 | Flash 이중기록 | 워치 내부 NAND에 동시 기록 (BLE 끊겨도 안전) |
| 3 | LT 자율로깅 | BLE 연결 없이도 워치 자체 기록 (최후의 보루) |
| 4 | 외부 백업 | 외장하드 → Google Drive → NAS (자동) |

### 자동 복구 흐름

```
BLE 끊김 발생
  ↓
watch_standalone: 120초간 데이터 없음 감지
  ↓
동글 sysfs 리셋 (USB re-authorize)
  ↓
SDK 재연결 시도 (최대 5회, 실패 시 다시 동글 리셋)
  ↓
연결 성공 → 센서 재시작 → 데이터 수집 재개
  ↓
(이중 안전망) monitor_ble2.sh: 2분마다 ppg.csv 체크 → 위와 동일
```

---

## 개발자용

### 로컬 테스트 (워치/Jetson 없이)

```bash
# 워치 에뮬레이터로 watch_standalone 테스트
WATCH_EMULATOR=1 python monitor/watch_standalone.py TEST001

# launcher GUI 테스트 (센서 목업)
python tests/test_launcher_sim.py              # 정상 모드
python tests/test_launcher_sim.py --watch-fail  # 워치 끊김
python tests/test_launcher_sim.py --all-fail    # 전체 장애

# 워치 에뮬레이터 단독 데모
python tests/watch_emulator.py
```

### Jetson 배포

```bash
# 로컬에서 수정 후
git add . && git commit -m "..." && git push

# Jetson에서
cd ~/Desktop/sensing-collector && git pull
```

### config.json 주요 설정

| 키 | 값 | 설명 |
|----|-----|------|
| `jetson.xhci_path` | `a80aa10000.usb` | USB 컨트롤러 경로 (Jetson 모델별 다름) |
| `device.watch_mac` | `F9:5A:50:8B:B2:F9` | 워치 BLE MAC 주소 |
| `device.watch_dongle_vid` | `0456` | 동글 USB Vendor ID |
| `monitor.ble_stale_threshold_sec` | `150` | BLE 끊김 판정 기준 (초) |

---

## 하드웨어

| 장비 | 모델 | VID:PID | 연결 |
|------|------|---------|------|
| 메인 PC | Jetson Orin NX (16GB) | - | 전원+HDMI+이더넷 |
| 카메라 정면 | Intel RealSense D435 | 8086:0b07 | USB 허브 |
| 카메라 측면 | Intel RealSense D435 | 8086:0b07 | USB 허브 |
| 마이크 | RODE Wireless GO II RX | 19f7:002a | USB 허브 |
| 워치 동글 | nRF52840 BLE Dongle | 0456:2cfe | USB 허브 |
| USB 허브 | Realtek RTS5411 | 0bda:5411 | **외부 전원 필수** |

---

## Jetson 환경

| 항목 | 값 |
|------|-----|
| IP | 165.132.48.241 (연세대 VPN 필요) |
| 계정 | jetson / jetson |
| OS | Ubuntu 24.04, Kernel 6.8.12-tegra |
| Python | miniforge3, conda env: `sensing` |
| xhci | `a80aa10000.usb` |
| 자동 로그인 | jetson (GDM3) |
| 자동 시작 | launcher.py (`~/.config/autostart/kmer-launcher.desktop`) |
| 바탕화면 경로 | `~/바탕화면/` (`~/Desktop/`이 아님!) |
| 코드 경로 | `~/Desktop/sensing-collector/` |
| VPN | ysvpn.yonsei.ac.kr (ID: 2025321053) |

---

## 알려진 이슈 + 해결 상태

| 이슈 | 원인 | 해결 | 자동? |
|------|------|------|------|
| LIBUSB_ERROR_BUSY | 동글 동시 접속 | v2.1에서 단일 소유자 구조로 해결 | O |
| 워치 BLE 끊김 | 하드웨어 특성 (20~40분) | watch_standalone 120초 타임아웃+자동 재연결 | O |
| GSR 간헐적 누락 | BLE 스케줄링 | Flash에 기록 중 → flash_download.py로 복구 | O |
| USB 안 잡힘 | xhci 불안정 | start.sh가 xhci 자동 리셋 | O |
| 디스크 풀 | 데이터 누적 | start.sh가 10GB 미만 차단 | O |
| 워치 슬립 | BLE 광고 중단 | **옆면 버튼 1초** (물리적 조작 필요) | X |
| activate_lt.py 후 BUSY | SDK disconnect 안 됨 | v2.1에서 disconnect+동글 리셋 추가 | O |

---

HEART Lab, Sejong University — RS-2024-00487049
