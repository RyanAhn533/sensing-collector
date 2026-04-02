# K-MER 센싱 시스템 변경 이력

---

## V3 (2026-04-02) — 내장 저장 + BLEManager 근본 패치

### 왜 바꿨나
- BLE 연결이 수시로 끊기면서 워치 데이터가 유실됨
- 하루 에러 18건, 전부 워치 BLE 문제
- SDK 내부 USB handle leak이 근본 원인 (아래 상세)

### 근본 원인 분석
SDK의 `BLEManager.connect()`가 `_open()`을 2번 호출하면서 첫 번째 USB handle을 release 안 함.
BLE 스캔 실패 시 USB interface가 claimed 상태로 leak → 모든 재시도가 LIBUSB_ERROR_BUSY로 실패.

```
워치 잠듦 → BLE 스캔 실패 → USB interface leak → 재시도 전부 BUSY → 무한 반복
```

### V1 → V3 주요 변경

#### 데이터 저장 방식
| | V1 | V3 |
|---|---|---|
| 저장 위치 | Jetson (BLE 스트리밍 → CSV) | **워치 내장 플래시** |
| BLE 역할 | 데이터 전송 (필수) | **상태 확인만** (없어도 됨) |
| BLE 끊기면 | 데이터 유실 | **영향 없음** |
| 실험 후 | 바로 CSV 확인 | 크레들 연결 → 다운로드 |

#### BLEManager 패치 (core/watch_v3.py)
| 문제 | V1 패치 | V3 패치 |
|------|---------|---------|
| resetDevice() 크래시 | 제거만 함 | 제거 |
| USB handle leak | 미해결 | **`_open()` 전 `_close()` 필수 호출** |
| receive_thread 종료 | 불가능 | **stop_event로 깨끗한 종료** |
| USBContext 관리 | 로컬 변수 (GC 의존) | **인스턴스 변수로 명시적 관리** |
| disconnect()에서 _open() | 그대로 (handle 3중 leak) | **_open() 호출 제거, 현재 handle로 처리** |
| connect()에서 handle leak | 미해결 | **reset 후 `_close()` → `_open()` 순서 보장** |

#### 센서 스트림
| 스트림 | V1 | V3 |
|--------|----|----|
| PPG (심박) | BLE 스트리밍 | **내장 플래시 기록** |
| EDA (피부전도) | BLE 스트리밍 | **내장 플래시 기록** |
| Temperature | BLE 스트리밍 | **내장 플래시 기록** |
| ADXL (가속도) | 없음 | **내장 플래시 기록** (motion artifact 감지용) |
| AGC (자동 gain) | 없음 | **활성화** (LED 밝기 자동 조절) |
| Battery 모니터링 | 없음 | **30초마다 폴링** |
| SQI (신호 품질) | 없음 | **활성화** (착용 시 동작) |

#### 에러 복구
| | V1 | V3 |
|---|---|---|
| 에러 분류 | 없음 (전부 동일 처리) | **5가지 타입 자동 분류** |
| BLE 못 찾음 | 동글 리셋 | "워치 깨워주세요" 알림 + 15초 대기 |
| USB BUSY | 동글 리셋 | 동글 하드 리셋, 3회 연속 시 xhci 리셋 |
| USB 장치 없음 | 동글 리셋 | xhci 전체 리셋 |
| 타임아웃 | 동글 리셋 | 동글 리셋 |

#### 런처 GUI
| | V1 | V3 |
|---|---|---|
| 워치 표시 | PPG/GSR/Temp 실시간 그래프 | **워치 상태 패널** (내장저장 O/X, 배터리, BLE) |
| 문제 해결 | 범용 위자드 | **문제별 "해결법" 버튼** (해당 위치에 자동 표시) |
| 데이터 백업 | 없음 | **"워치 내장데이터 백업" 버튼** |
| 매뉴얼 | 기술 문서 | **문과 친화적 매뉴얼** |
| LED 표시 | 7개 (카메라, 마이크, 동글, 영상x2, 음성, PPG, GSR, 온도) | **워치 상태 패널 + 장비 LED 5개** |

---

### 파일 변경 목록

#### 새로 만든 파일
| 파일 | 설명 |
|------|------|
| `core/watch_v3.py` | BLEManager 완전 패치 + 플래시 로깅 + 상태 폴링 API |
| `monitor/watch_standalone_v3.py` | 상태 폴링 모드 (30초마다 상태 체크 → JSON 기록) |
| `launcher_v3.py` | 새 GUI (워치 상태 패널 + 해결법 버튼 + 백업 버튼) |
| `ops/start_v3.sh` | V3 시작 스크립트 |
| `ops/stop_v3.sh` | V3 종료 스크립트 |
| `ops/flash_download.py` | 크레들 연결 시 내장 플래시 다운로드 + CSV 변환 |
| `docs/MANUAL_V3.md` | 문과 친화적 운영 매뉴얼 |
| `docs/CHANGELOG_V3.md` | 이 문서 |
| `tests/sdk_feature_test.py` | SDK 32개 기능 호환성 테스트 |

#### 중간 과정 파일 (V2, 롤백용으로 남겨둠)
| 파일 | 설명 |
|------|------|
| `core/watch_v2.py` | V2 센싱 엔진 (6스트림 BLE 스트리밍) |
| `monitor/watch_standalone_v2.py` | V2 지능형 에러 복구 |
| `monitor/monitor_ble2_v2.sh` | V2 BLE 모니터 |
| `ops/start_v2.sh` | V2 시작 |
| `ops/stop_v2.sh` | V2 종료 |

#### 기존 파일 (V1, 건드리지 않음)
| 파일 | 설명 |
|------|------|
| `core/watch.py` | 원본 센싱 엔진 |
| `monitor/watch_standalone.py` | 원본 워치 프로세스 |
| `monitor/monitor_ble2.sh` | 원본 BLE 모니터 |
| `ops/start.sh` | 원본 시작 |
| `ops/stop.sh` | 원본 종료 |
| `launcher.py` | 원본 런처 (V2 연결로 수정됨) |

#### 수정한 기존 파일
| 파일 | 변경 내용 |
|------|----------|
| `launcher.py` | start.sh → start_v2.sh, stop.sh → stop_v2.sh 연결, LED에 가속도/SQI/배터리 추가 |

---

### 테스트 결과 (2026-04-02)

| 항목 | 결과 |
|------|------|
| 펌웨어 버전 | 5.22.1 (SDK와 동일, 호환성 문제 없음) |
| SDK 기능 호환성 | 32개 중 29개 OK, 3개 FAIL (API명 차이뿐) |
| V2 실시간 테스트 (30초) | PPG 171, EDA 312, Temp 11, ADXL 102, Ped 10 — 전부 정상 |
| V3 플래시 로깅 | flash_logging=true, 7개 파일, 배터리 100% |
| V3 런처 GUI | 정상 실행, 센싱 시작/종료 확인 |
| 구글 드라이브 백업 | C001~C044 전부 업로드 완료 |

---

### 롤백 방법

V3에 문제가 있으면 V1으로 돌아갈 수 있습니다:

```bash
# launcher.py에서 start_v2.sh → start.sh, stop_v2.sh → stop.sh로 되돌리기
# 또는 직접 실행:
bash ops/start.sh C001
bash ops/stop.sh
```

V1 파일은 전부 그대로 남아있습니다.
