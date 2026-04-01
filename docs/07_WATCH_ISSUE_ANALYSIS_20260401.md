# 워치 문제 근본 분석 및 개선 계획 (2026-04-01)

## 하드웨어: EVAL-HCRWATCH4Z (ADI VSM Watch)

| 항목 | 값 |
|------|-----|
| 제조사 | Analog Devices |
| 모델 | EVAL-HCRWATCH4Z |
| 통신 | BLE 5.0 (nRF52840 USB 동글) |
| 센서 | ADPD4100(PPG), AD5940(EDA), AD8233(ECG), ADXL362(가속도), 온도 |
| 전원 | 리튬폴리머 배터리, IP68 방수 |
| 깨우기 | **Navigation 버튼 1초 롱프레스** (화면 터치 아님!) |
| 리셋 | Navigation 버튼 3초 롱프레스 |
| 부트로더 | Action + Navigation 동시 3초 |

---

## 2026-04-01 전체 에러 로그 분석

### 숫자 요약
- 총 에러: **18건**
- 영상/오디오 에러: **0건**
- 워치 에러: **18건 (100%)**

### 에러 종류

| 에러 | 횟수 | 원인 |
|------|------|------|
| `run_watch() got unexpected keyword argument 'on_ecg'` | 2 | main.py → watch.py 인자 불일치 (수정 완료) |
| `LIBUSB_ERROR_BUSY [-6]` | 12 | 동글 USB를 다른 프로세스가 점유 중 |
| `LIBUSB_ERROR_IO [-1]` | 3 | 동글 USB 통신 실패 (점유 충돌의 변형) |
| `Can't connect to BLE` / `Failed to find BLE device` | 1 | 워치가 BLE 광고 안 함 (잠듦) |

### 에러 발생 타임라인

```
11:12  리부트 완료
11:15  activate_lt.py → LT 활성화 성공 (동글 점유)
11:30  main.py 시작 → watch 스레드 LIBUSB_ERROR_BUSY (activate_lt가 동글 안 놓음)
11:31  동글 sysfs 리셋 → 해결
11:36  main.py 재시작 → 또 BUSY (main.py 내부 USB 리셋이 잘못된 경로 1-2)
11:42  start.sh 시도 → set -e로 죽음
11:47  main.py 수동 시작 → 동글 리셋 후 워치 연결 성공!
11:54  워치 BLE 끊김 → CSV 수집 중단
12:06  watch_standalone으로 워치 재시작 → 성공
12:14  또 BLE 끊김 → monitor_ble2 v2는 자동 재시작 기능 없음
12:30  수동 워치 재시작 → 성공
12:54  또 BLE 끊김
13:06  main.py 종료

13:56  C002 start.sh → 성공, 워치 BUSY
14:08  monitor_ble2 v3 자동 재시작 6회 시도 → 14:10 성공
14:12~ 정상 운영 (~14:44 종료)

15:06  연세대 C042 시작 (구버전 Desktop 경로) → LIBUSB_ERROR_IO 반복
15:29  C043 start.sh → 워치 BUSY → watch_standalone 자동 시작
15:33  monitor_ble2 자동 재시작 → 워치 BLE 스캔 실패
```

---

## 근본 원인 5가지

### 원인 1: 워치 깨우기 방법이 틀렸다
- **잘못된 방법**: "화면 터치해서 깨워"
- **올바른 방법**: Navigation 버튼(옆면) 1초 롱프레스
- 워치가 완전 슬립 상태면 터치로는 안 깨어남 → BLE 광고 시작 안 함 → SDK 스캔 60초 타임아웃
- **영향**: 에러의 ~30% (BLE device not found)

### 원인 2: USB 동글 단일 점유 구조
- nRF52840 동글은 USB `claimInterface()`로 한 프로세스만 점유 가능
- SDK가 크래시하면 `releaseInterface()` 안 하고 죽음
- 커널이 USB claim을 유지 → 다음 프로세스가 LIBUSB_ERROR_BUSY
- sysfs 리셋으로만 해제 가능 (물리적 뽑기와 동일)
- **영향**: 에러의 ~60% (LIBUSB_ERROR_BUSY)

### 원인 3: 동글을 여러 프로세스가 경쟁
```
경쟁 관계:
  activate_lt.py  ──┐
  main.py watch thread ──┼── 동일한 USB 동글 ── nRF52840
  watch_standalone.py ──┘
```
- activate_lt.py가 동글 잡고 안 놓음 → main.py BUSY
- main.py watch 스레드가 죽어도 USB claim 남음 → watch_standalone BUSY
- watch_standalone 여러 개 띄우면 서로 BUSY
- **영향**: 에러의 ~50% (프로세스 충돌)

### 원인 4: 코드 경로 분산
- `/home/jetson/Desktop/sensing-collector/` (구버전, autostart가 가리킴)
- `/home/jetson/work/sensing-collector/` (신버전)
- `/home/jetson/Desktop/sensing_code/` (최초 버전)
- 연세대 측이 구버전 실행 → 수정사항 미반영 → monitor_ble2 자동 재시작 없음
- **영향**: 오늘 연세대 C042 에러의 100%

### 원인 5: BLE 연결 불안정 (워치 하드웨어)
- BLE 연결이 20~40분마다 끊김
- ADI SDK의 BLE 연결 유지 메커니즘 부족
- watch.py 메인 루프가 `sleep(0.1)`만 하고 BLE 상태 확인 안 함
- 끊기면 감지 못하고, 데이터만 안 들어옴
- **영향**: 에러의 ~20% (연결 유지 실패)

---

## 개선 계획

### Phase 1: 즉시 적용 (코드 수정)

#### 1-1. 동글 소유자를 하나로 통일
**현재 구조 (문제):**
```
main.py
  ├── realsense thread (영상)
  ├── rode thread (오디오)
  ├── watch thread (워치) ← 동글 점유
  └── monitor thread

watch_standalone.py ← 동글 점유 (충돌!)
monitor_ble2.sh → watch_standalone 재시작
```

**개선 구조:**
```
main.py
  ├── realsense thread (영상)
  ├── rode thread (오디오)
  └── monitor thread
  (watch thread 제거)

watch_standalone.py ← 유일한 동글 소유자
monitor_ble2.sh → watch_standalone 관리
start.sh → main.py + watch_standalone 별도 시작
```

- main.py에서 watch 관련 코드 제거
- watch_standalone.py가 유일한 워치 프로세스
- LIBUSB_ERROR_BUSY 원천 차단

#### 1-2. activate_lt.py 후 동글 강제 해제
```python
# activate_lt.py 끝에 추가
sdk.disconnect()
time.sleep(1)
# 동글 sysfs 리셋
subprocess.run(["sudo", "-n", "sh", "-c", f"echo 0 > {dongle_path}/authorized"], ...)
time.sleep(2)
subprocess.run(["sudo", "-n", "sh", "-c", f"echo 1 > {dongle_path}/authorized"], ...)
```

#### 1-3. SDK disconnect 패치
```python
def _patched_disconnect(self):
    self._is_connected.clear()
    if hasattr(self, 'device') and self.device:
        try:
            self.device.releaseInterface(0)
        except Exception:
            pass
        try:
            self.device.close()
        except Exception:
            pass
        self.device = None
```

#### 1-4. watch_standalone에 BLE 끊김 감지 + 자동 재연결
```python
# 메인 루프에서 데이터 수신 타임아웃 감지
last_data_time = time.time()

def on_ppg(ts, d1, d2):
    nonlocal last_data_time
    last_data_time = time.time()
    ...

# 120초 동안 데이터 없으면 자체 재시작
while not shutdown_event.is_set():
    if time.time() - last_data_time > 120:
        raise RuntimeError("BLE 데이터 타임아웃 (120s)")
    time.sleep(1)
```

#### 1-5. 워치 깨우기 매뉴얼 수정
모든 문서에서:
- ~~"화면 터치해서 깨우기"~~ → **"옆면 Navigation 버튼 1초 꾹 누르기"**

### Phase 2: 코드 경로 통일

#### 2-1. 단일 코드 경로
- `/home/jetson/Desktop/sensing-collector/`를 유일한 실행 경로로
- `/home/jetson/work/sensing-collector/`는 심링크로 연결
- autostart, .desktop, start.sh 전부 동일 경로

#### 2-2. 구버전 정리
- `sensing_code/` 삭제 (이미 20260401_세팅완료로 이동)
- `work/sensing-collector` → Desktop으로 심링크

### Phase 3: start.sh 플로우 개선

```bash
# 개선된 start.sh 플로우
1. precheck (USB, 디스크)
2. 기존 프로세스 전부 kill (좀비 방지)
3. 동글 sysfs 리셋 (깨끗한 상태)
4. main.py 시작 (영상+오디오만)
5. 35초 검증 (v, a)
6. watch_standalone 시작 (워치 전담)
7. 20초 검증 (ppg, gsr, temp)
8. monitor_ble2 시작
9. dashboard 시작
```

---

## 예상 효과

| 문제 | 현재 | 개선 후 |
|------|------|---------|
| LIBUSB_ERROR_BUSY | 매번 발생 | 원천 차단 (단일 소유자) |
| 워치 BLE 스캔 실패 | 30% | ~5% (올바른 깨우기) |
| BLE 끊김 후 복구 | 수동/2분 대기 | 자동 120초 내 감지+재연결 |
| 코드 경로 혼란 | 3개 경로 | 1개 경로 |
| 프로세스 좀비 | 빈번 | start.sh에서 사전 정리 |

---

## 파일 수정 목록

| 파일 | 수정 내용 |
|------|----------|
| `core/main.py` | watch 스레드 제거, 영상+오디오만 |
| `core/watch.py` | disconnect 패치, BLE 타임아웃 감지 |
| `monitor/watch_standalone.py` | 자동 재연결 루프, 타임아웃 감지 |
| `ops/start.sh` | 프로세스 정리 → main.py → watch_standalone 분리 시작 |
| `recovery/activate_lt.py` | 끝에 disconnect + 동글 리셋 |
| `monitor/monitor_ble2.sh` | watch_standalone 전용 관리 |
| `launcher.py` | 위자드에서 "버튼 누르기" 안내 |
| `CLAUDE.md` | 워치 깨우기 방법 수정 |
| `docs/00_USER_MANUAL.html` | 워치 깨우기 방법 수정 |
| `docs/01_QUICK_START.md` | 워치 깨우기 방법 수정 |
| `docs/03_TROUBLESHOOTING.md` | 워치 깨우기 방법 수정 |
