#!/usr/bin/env python3
"""
자동 센싱 시스템
- 카메라로 사람이 앉아있는지 감지
- 10초 이상 감지되면 자동 센싱 시작
- 센싱 종료 후 다시 감지 모드로 복귀
- 모니터 화면에 상태 표시
"""
import cv2
import numpy as np
import pyrealsense2 as rs
import subprocess
import os
import sys
import time
import glob
import signal
import threading

DATA_DIR = "/home/jetson/Desktop/sensing_code/data"
SENSING_DIR = "/home/jetson/Desktop/sensing_code"
ENV_PY = "/home/jetson/anaconda3/envs/sensing/bin/python"
MAIN_PY = os.path.join(SENSING_DIR, "main.py")
PID_FILE = "/tmp/sensing_main.pid"
LOCK_FILE = "/tmp/sensing_detect.lock"

# 감지 설정
DETECT_SECONDS = 10       # 이 시간 동안 연속 감지되면 시작
ABSENCE_SECONDS = 120     # 센싱 중 이 시간 동안 데이터 안 들어오면 종료 고려
CHECK_FPS = 2             # 감지 체크 빈도 (초당)
CAM_WIDTH, CAM_HEIGHT = 640, 480

# 얼굴 감지기
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
body_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_upperbody.xml")


def get_next_participant_id():
    """다음 참가자 번호 자동 생성."""
    existing = glob.glob(os.path.join(DATA_DIR, "C[0-9][0-9][0-9]"))
    if existing:
        nums = [int(os.path.basename(d)[1:]) for d in existing]
        return f"C{max(nums)+1:03d}"
    return "C001"


def detect_person(frame):
    """프레임에서 사람(얼굴 또는 상체) 감지."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)

    faces = face_cascade.detectMultiScale(gray, 1.3, 5, minSize=(60, 60))
    if len(faces) > 0:
        return True, faces, "face"

    bodies = body_cascade.detectMultiScale(gray, 1.1, 3, minSize=(80, 80))
    if len(bodies) > 0:
        return True, bodies, "body"

    return False, [], None


def draw_status(frame, state, detect_count, detect_needed, participant_id=None):
    """프레임에 상태 오버레이."""
    h, w = frame.shape[:2]

    # 반투명 상단 바
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 50), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    if state == "waiting":
        text = "대기 중 - 의자에 앉으면 센싱이 시작됩니다"
        color = (100, 100, 255)
        if detect_count > 0:
            progress = min(detect_count / detect_needed, 1.0)
            bar_w = int(w * progress)
            cv2.rectangle(frame, (0, 45), (bar_w, 50), (0, 255, 100), -1)
            text = f"감지 중... {detect_count}/{detect_needed}초"
            color = (0, 255, 100)
    elif state == "sensing":
        text = f"센싱 중 - {participant_id}"
        color = (0, 255, 0)
    elif state == "starting":
        text = "센싱 시작 중..."
        color = (0, 200, 255)

    cv2.putText(frame, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return frame


def start_sensing(participant_id):
    """센싱 프로세스 시작."""
    env = os.environ.copy()
    env["PARTICIPANT_ID"] = participant_id

    proc = subprocess.Popen(
        [ENV_PY, "-u", MAIN_PY],
        cwd=SENSING_DIR,
        env=env,
        stdout=open(f"{SENSING_DIR}/logs/{participant_id}_auto.log", "w"),
        stderr=subprocess.STDOUT,
    )

    # PID 저장
    with open("/tmp/sensing_main.pid", "w") as f:
        f.write(str(proc.pid))

    return proc


def stop_sensing(proc):
    """센싱 프로세스 안전 종료."""
    if proc and proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        os.remove("/tmp/sensing_main.pid")
    except Exception:
        pass


def is_sensing_alive(proc):
    """센싱 프로세스가 살아있는지."""
    return proc is not None and proc.poll() is None


def is_sensing_running():
    """수동 센싱이 이미 실행 중인지 확인."""
    if os.path.exists(PID_FILE):
        try:
            pid = int(open(PID_FILE).read().strip())
            return os.path.exists(f"/proc/{pid}")
        except Exception:
            pass
    return False


def main():
    print("[AUTO] 자동 센싱 시스템 시작", flush=True)

    # 이미 센싱 중이면 종료
    if is_sensing_running():
        print("[AUTO] 센싱이 이미 실행 중. 자동 센싱 종료.", flush=True)
        try:
            subprocess.Popen(
                ["zenity", "--info", "--title", "자동 센싱", "--text",
                 "센싱이 이미 실행 중입니다.\n자동 센싱을 시작할 수 없습니다."],
                env={**os.environ, "DISPLAY": ":1",
                     "XAUTHORITY": "/run/user/1000/gdm/Xauthority"},
            )
        except Exception:
            pass
        return

    # 잠금 파일 생성
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

    # 블루투스 끄기
    subprocess.run(["sudo", "-n", "systemctl", "stop", "bluetooth"],
                   capture_output=True, timeout=5)

    state = "waiting"
    detect_count = 0
    detect_needed = DETECT_SECONDS * CHECK_FPS
    sensing_proc = None
    participant_id = None
    last_frame = None

    # 윈도우 설정
    cv2.namedWindow("Auto Sensing", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Auto Sensing", 640, 480)

    while True:
        if state == "waiting":
            # RealSense로 감지
            try:
                pipe = rs.pipeline()
                cfg = rs.config()
                # 메인 카메라만 저해상도로 감지용 (센싱 시작 시 해제)
                cfg.enable_device("021222070391")
                cfg.enable_stream(rs.stream.color, CAM_WIDTH, CAM_HEIGHT, rs.format.bgr8, 6)
                pipe.start(cfg)

                while state == "waiting":
                    frames = pipe.wait_for_frames(3000)
                    color_frame = frames.get_color_frame()
                    if not color_frame:
                        continue

                    frame = np.asarray(color_frame.get_data())
                    detected, rects, dtype = detect_person(frame)

                    if detected:
                        detect_count += 1
                        for (x, y, w, h) in rects:
                            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                    else:
                        detect_count = max(0, detect_count - 1)

                    frame = draw_status(frame, "waiting", detect_count // CHECK_FPS, DETECT_SECONDS)
                    cv2.imshow("Auto Sensing", frame)
                    last_frame = frame.copy()

                    key = cv2.waitKey(int(1000 / CHECK_FPS)) & 0xFF
                    if key == 27 or key == ord('q'):
                        pipe.stop()
                        cv2.destroyAllWindows()
                        return

                    # 충분히 감지됨 → 센싱 시작
                    if detect_count >= detect_needed:
                        state = "starting"
                        break

                pipe.stop()
                time.sleep(2)  # 카메라 해제 대기

            except Exception as e:
                print(f"[AUTO] 카메라 에러: {e}", flush=True)
                time.sleep(5)
                continue

        elif state == "starting":
            participant_id = get_next_participant_id()
            print(f"[AUTO] 사람 감지! 센싱 시작: {participant_id}", flush=True)

            # 상태 화면 표시
            if last_frame is not None:
                f = draw_status(last_frame, "starting", 0, 0, participant_id)
                cv2.imshow("Auto Sensing", f)
                cv2.waitKey(1000)

            sensing_proc = start_sensing(participant_id)
            state = "sensing"
            detect_count = 0
            sensing_start_time = time.time()

        elif state == "sensing":
            # 센싱 중 모니터링 화면
            info_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            info_frame[:] = (30, 30, 30)

            elapsed = int(time.time() - sensing_start_time)
            mins, secs = divmod(elapsed, 60)

            cv2.putText(info_frame, f"SENSING: {participant_id}",
                        (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 100), 2)
            cv2.putText(info_frame, f"Time: {mins:02d}:{secs:02d}",
                        (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)

            # 데이터 상태 확인
            save_root = os.path.join(DATA_DIR, participant_id)
            minute_dirs = sorted(glob.glob(os.path.join(save_root, "20*")))
            if minute_dirs:
                latest = minute_dirs[-1]
                files = os.listdir(latest)
                y = 180
                sensors = {
                    "Video": any("video" in f for f in files),
                    "Audio": any("audio" in f and os.path.getsize(os.path.join(latest, f)) > 100 for f in files),
                    "PPG": any(f.startswith("ppg") for f in files),
                    "ADXL": any(f.startswith("adxl") for f in files),
                    "Temp": any(f.startswith("temp") for f in files),
                    "GSR": any(f.startswith("gsr") for f in files),
                }
                for name, ok in sensors.items():
                    color = (0, 255, 100) if ok else (0, 0, 255)
                    symbol = "●" if ok else "✕"
                    cv2.putText(info_frame, f"{symbol} {name}",
                                (30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    y += 35

            cv2.putText(info_frame, "[ESC] 센싱 종료",
                        (30, 440), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 100), 1)

            cv2.imshow("Auto Sensing", info_frame)
            key = cv2.waitKey(2000) & 0xFF

            if key == 27 or key == ord('q'):
                print(f"[AUTO] 수동 종료: {participant_id}", flush=True)
                stop_sensing(sensing_proc)
                sensing_proc = None
                state = "waiting"
                detect_count = 0
                time.sleep(3)
                continue

            # 프로세스가 죽었으면 대기 모드로
            if not is_sensing_alive(sensing_proc):
                print(f"[AUTO] 센싱 프로세스 종료됨: {participant_id}", flush=True)
                sensing_proc = None
                state = "waiting"
                detect_count = 0
                time.sleep(3)

    cv2.destroyAllWindows()
    # 잠금 파일 제거
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass


if __name__ == "__main__":
    main()
