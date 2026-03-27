# Sensing Collector

**차량 시뮬레이터 피실험자 멀티모달 데이터 수집 시스템**

Jetson Orin Nano에서 운전 시뮬레이션 실험 중 피실험자의 영상, 음성, 생체신호를 동시에 수집한다.

---

## 한줄 요약

> RealSense 카메라 2대 + RODE 무선 마이크 + ADI 워치(심박/피부전도/체온)로 운전 중 데이터를 1분 단위로 저장하고, 자동 모니터링 + 오프라인 검증으로 데이터 소실을 방지한다.

---

## 수집하는 데이터

| 센서 | 데이터 | 샘플레이트 | 파일 |
|------|--------|-----------|------|
| RealSense D435 (정면) | 1920x1080 H.264 영상 | 30fps | video_main.mp4 |
| RealSense D435 (측면) | 1920x1080 H.264 영상 | 30fps | video_sub.mp4 |
| RODE Wireless GO II | 48kHz 모노 음성 | 48000Hz | audio.wav |
| ADI Study Watch - PPG | 심박(광혈류) | 100Hz | ppg.csv |
| ADI Study Watch - EDA | 피부전도(GSR) | 30Hz | gsr.csv |
| ADI Study Watch - Temp | 피부온도 | 1Hz | temp.csv |

**저장 구조:**
```
data/C039/
  ├── 20260327_1015/     ← 1분 단위 폴더
  │   ├── video_main.mp4
  │   ├── video_sub.mp4
  │   ├── audio.wav
  │   ├── ppg.csv
  │   ├── gsr.csv
  │   └── temp.csv
  ├── 20260327_1016/
  │   └── ...
  └── ...
```

---

## 실험 진행 순서

### 1단계: 실험 전 준비

```bash
# Jetson에 SSH 접속
ssh yonsei

# 프로젝트 폴더로 이동
cd ~/Desktop/sensing-collector

# 장치 확인
ls /dev/ttyACM*    # 워치 동글
ls /dev/video*     # 카메라
lsusb              # 전체 USB
df -h /            # 디스크 여유 (최소 10GB 필요)
```

**체크리스트:**
- [ ] 워치를 충전 크래들에서 빼기
- [ ] 워치 화면 터치해서 깨우기
- [ ] RODE TX 전원 켜기
- [ ] 카메라 위치/각도 확인

### 2단계: 센싱 시작

```bash
# 자동 시작 (precheck + sensing + monitoring 한번에)
./ops/start.sh C040

# 또는 환경변수로
PARTICIPANT_ID=C040 ./ops/start.sh
```

**start.sh가 자동으로 하는 것:**
1. USB 장치 확인 → 안 잡히면 xhci 리셋
2. 디스크 여유 확인 (10GB 미만이면 중단)
3. 동글 리셋 → main.py 실행
4. 35초 후 센서 상태 확인: `v=OK a=OK ppg=OK gsr=OK temp=OK`
5. monitor_ble2.sh 자동 시작

### 3단계: 실험 중 확인

```bash
# 센서 상태 확인 (가장 최근 1분 폴더)
ls -la data/C040/$(ls -t data/C040/ | head -1)

# 실시간 로그
tail -f logs/C040_sensing.log

# 모니터 로그 (워치 BLE 상태)
tail -f logs/monitor_ble.log
```

**실험 중 절대 하지 말 것:**
- USB 케이블 건드리기
- 코드 수정
- 프로세스 kill
- monitor_ble2.sh 끄기

### 4단계: 센싱 종료

```bash
./ops/stop.sh
```

### 5단계: 즉석 데이터 검증 (현장에서!)

```bash
# 오프라인 검증 — 네트워크 없어도 동작
python3 validate/validate_session.py data/C040
```

출력 예시:
```
============================================================
  SESSION VALIDATION: C040
============================================================
  Duration: 120 minutes
  Range: 20260327_1015 ~ 20260327_1215
  OK: 118  Warnings: 2  Errors: 0

  Missing modalities:
    gsr: 2 minutes missing

  VERDICT: PASS_WITH_WARNINGS
============================================================
```

**PASS면 다음 실험 가능. FAIL이면 원인 파악 후 재수집.**

### 6단계: 데이터 백업

```bash
# 1. 외장하드에 복사 (원본 보존)
cp -r data/C040 /media/jetson/외장하드/

# 2. NAS로 전송
# (네트워크 가능할 때)

# 3. 검증 후에만 로컬 삭제
python3 validate/validate_session.py data/C040  # 다시 한번 확인
```

---

## 프로젝트 구조

```
sensing-collector/
├── config.json            # 장비 시리얼, 경로, 임계값 설정
│
├── core/                  # 센서 드라이버
│   ├── main.py            # 오케스트레이터 (스레드 관리)
│   ├── watch.py           # ADI Study Watch BLE (PPG/EDA/Temp)
│   ├── realsense.py       # RealSense D435 (GStreamer H.264)
│   ├── rode.py            # RODE 무선 마이크 (WAV)
│   └── rt_pub.py          # ZeroMQ 실시간 퍼블리셔
│
├── ops/                   # 운영 스크립트
│   ├── start.sh           # 센싱 시작 (precheck→sensing→monitor)
│   ├── stop.sh            # 안전 종료 + 즉석 검증 안내
│   ├── switch.sh          # 참가자 전환
│   ├── precheck.sh        # 장치 사전 체크
│   ├── daily_pipeline.sh  # 하루 전체 자동 운영
│   ├── schedule_example.sh # 시간대별 스케줄 예시
│   ├── post_sensing.sh    # 종료 후 처리
│   └── verify_copy.sh     # 백업 검증
│
├── monitor/               # 실시간 모니터링
│   ├── monitor_ble2.sh    # 워치 BLE 감시 + 자동 재시작 (핵심!)
│   ├── monitor_sensing.sh # 영상 감시 (systemd)
│   ├── monitor.py         # 데이터 모니터 (Python)
│   └── watchdog.sh        # 프로세스 워치독
│
├── validate/              # 데이터 검증 (오프라인 가능!)
│   └── validate_session.py # 세션 전체 검증
│       - 파일 존재 확인
│       - MP4 moov atom 검사 (영상 finalize 확인)
│       - WAV 헤더 검사
│       - CSV 행수 + 파싱 확인
│       - 타임스탬프 연속성 (갭 감지)
│       - PASS / PASS_WITH_WARNINGS / FAIL 판정
│
├── recovery/              # 복구 도구
│   ├── flash_download.py  # 워치 Flash 데이터 다운로드
│   ├── recover_watch.py   # Flash→CSV 복원
│   ├── activate_lt.py     # LT 자율 로깅 활성화
│   └── test_eda_now.py    # EDA 단독 테스트
│
└── dcb_cfg/               # 워치 센서 설정 파일 (DCB)
```

---

## 데이터 보호 전략

### 3중 보호

| 계층 | 방법 | 자동? | 복구 가능? |
|------|------|------|-----------|
| 1. BLE CSV | 실시간 BLE 스트림 → 1분 CSV | O | 바로 사용 |
| 2. Flash 이중기록 | fs_subscribe → 워치 NAND | O | flash_download.py |
| 3. LT 자율 로깅 | 워치 자체 로깅 (BLE 끊겨도) | O | recover_watch.py |

### 모니터링

| 감시 대상 | 스크립트 | 주기 | 자동 복구 |
|----------|---------|------|----------|
| 워치 BLE | monitor_ble2.sh | 2분 | 동글 리셋 + 재시작 |
| 영상 | monitor_sensing.sh | 5분 | 센싱 재시작 |
| 데이터 무결성 | main.py monitor_data | 30초 | 팝업 경고 |

---

## 알려진 이슈 (Orin Nano)

| 이슈 | 원인 | 해결 |
|------|------|------|
| 리부트 후 USB 안 잡힘 | xhci 3610000.usb 불안정 | start.sh가 자동 xhci 리셋 |
| EDA(GSR) BLE 안 나옴 | 센서 시작 순서 민감 | ADPD→Temp→EDA 순서 강제 |
| LIBUSB_ERROR_BUSY | 동글 잠김 | killall -9 python → 동글 리셋 |
| 워치 연결 안 됨 | 워치 슬립 모드 | 물리적 화면 터치 필요 |
| 디스크 풀 | 1시간 ~15GB | start.sh가 10GB 미만이면 차단 |
| MP4 손상 | GStreamer 비동기 close | .tmp→final rename으로 방어 |

---

## 트러블슈팅

### 워치 안 잡힘
```bash
# 1. 워치 화면 터치해서 깨우기
# 2. 동글 리셋
bash recovery/usb_reset.sh dongle
# 3. 안 되면 xhci 전체 리셋
bash recovery/usb_reset.sh xhci
# 4. 그래도 안 되면 리부트
sudo reboot
```

### EDA(GSR) 데이터 안 나옴
```bash
# Flash에 기록 중이므로 BLE 안 나와도 데이터는 보존됨
# 실험 후 Flash 다운로드:
python3 recovery/flash_download.py
```

### 영상 끊김
```bash
# monitor_sensing.sh가 5분 내 자동 재시작
# 수동 확인:
ls -la data/C040/$(ls -t data/C040/ | head -1)/video_main*
```

---

## 하드웨어

| 장비 | 모델 | 비고 |
|------|------|------|
| 컴퓨터 | Jetson Orin Nano | 165.132.48.241 |
| 카메라 (정면) | Intel RealSense D435 | SN: 021222070391 |
| 카메라 (측면) | Intel RealSense D435 | SN: 405622073483 |
| 마이크 | RODE Wireless GO II | TX 내장 녹음 백업 |
| 워치 | ADI Study Watch | MAC: F9:5A:50:8B:B2:F9 |
| BLE 동글 | ADI USB Dongle | SN: C832CD764DD7 |
| USB 허브 | Realtek RTS5411 | 외부 전원 필수! |
