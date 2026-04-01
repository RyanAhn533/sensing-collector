import time
import datetime
import os
import threading
import faulthandler
import subprocess
import glob
# csv는 watch_standalone.py에서 사용

# --- 센서 모듈 ---
from realsense import run_realsense
from rode import run_rode
# watch는 watch_standalone.py에서 별도 프로세스로 실행 (동글 경쟁 방지)

# 2cam 시리얼 번호
RS_MAIN_SERIAL = "021222070391"
RS_SUB_SERIAL = "405622073483"

DATA_DIRECTORY = "data"


def get_participant_info():
    # 환경변수에서 먼저 확인 (GUI 런처에서 전달)
    participant_id = os.environ.get("PARTICIPANT_ID", "").strip()
    if participant_id:
        return participant_id

    # 자동 순번: 기존 C### 폴더 중 가장 큰 번호 + 1
    existing = glob.glob(os.path.join(DATA_DIRECTORY, "C[0-9][0-9][0-9]"))
    if existing:
        nums = [int(os.path.basename(d)[1:]) for d in existing]
        next_id = f"C{max(nums)+1:03d}"
    else:
        next_id = "C001"

    # 터미널 입력
    while True:
        participant_id = input(f"참가자ID 입력 (엔터 = {next_id}): ").strip()
        if not participant_id:
            participant_id = next_id
        print(f"참가자ID: {participant_id}")
        confirm = input("맞으면 엔터, 다시 입력하려면 n: ").strip()
        if confirm.lower() != "n":
            return participant_id


def notify_popup(title, message):
    """화면에 경고 팝업을 띄운다."""
    try:
        subprocess.Popen(
            ["zenity", "--warning", "--title", title, "--text", message, "--timeout", "10"],
            env={**os.environ, "DISPLAY": ":1", "XAUTHORITY": "/run/user/1000/gdm/Xauthority"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def ask_claude_for_help(name, error_msg, traceback_str):
    """센서 에러 발생 시 Claude에게 자동으로 수리 요청."""
    try:
        prompt = (
            f"센싱 시스템에서 [{name}] 센서에 에러가 발생했습니다. "
            f"에러: {error_msg}\n"
            f"트레이스백:\n{traceback_str}\n\n"
            f"이 에러를 분석하고 가능하면 자동으로 고쳐주세요. "
            f"코드 경로: /home/jetson/Desktop/sensing_code/"
        )
        log_path = f"logs/{name}_claude_fix.txt"
        result = subprocess.run(
            ["claude", "--print", "-p", prompt],
            capture_output=True, text=True, timeout=120,
            cwd="/home/jetson/Desktop/sensing_code"
        )
        with open(log_path, "a") as f:
            f.write(f"\n{'='*50}\n{datetime.datetime.now()}\n")
            f.write(f"에러: {error_msg}\n")
            f.write(f"Claude 응답:\n{result.stdout}\n")
        print(f"[{name}] Claude 진단 결과가 {log_path}에 저장됨")
    except Exception as ce:
        print(f"[{name}] Claude 호출 실패: {ce}")


def safe_run_with_retry(target, name, max_retries=3, *args, **kwargs):
    """센서 스레드 래퍼: 크래시 시 자동 재시도 + 경고 팝업 + Claude 진단."""
    def wrapper():
        for attempt in range(max_retries):
            try:
                target(*args, **kwargs)
                return  # 정상 종료
            except Exception as e:
                import traceback
                tb_str = traceback.format_exc()
                print(f"\n[{name}] 에러 발생 (시도 {attempt+1}/{max_retries}): {e}")
                traceback.print_exc()

                # 로그 기록
                try:
                    with open(f"logs/{name}_error.txt", "a") as f:
                        f.write(f"{datetime.datetime.now()} - attempt {attempt+1} - {e}\n")
                        f.write(tb_str + "\n")
                except Exception:
                    pass

                # 경고 팝업
                notify_popup(f"{name} 에러", f"{name} 센서 에러!\n{e}\n재시도 {attempt+1}/{max_retries}")

                if attempt < max_retries - 1:
                    print(f"[{name}] 10초 후 재시도...")
                    time.sleep(10)
                else:
                    # 마지막 시도 실패 — Claude 진단
                    notify_popup(f"{name} 실패", f"{name} 센서 {max_retries}회 실패.\nClaude 진단 중...")
                    ask_claude_for_help(name, str(e), tb_str)
    return wrapper


def monitor_data(save_root, shutdown_event):
    """30초마다 데이터가 정상 저장되고 있는지 확인. 문제 있으면 팝업 경고."""
    time.sleep(30)  # 시작 후 30초 대기
    while not shutdown_event.is_set():
        try:
            # 최신 분 폴더 확인
            minute_dirs = sorted(glob.glob(os.path.join(save_root, "20*")))
            if not minute_dirs:
                time.sleep(30)
                continue

            latest = minute_dirs[-1]
            files = os.listdir(latest)
            missing = []

            # 비디오 확인
            has_video = any("video" in f for f in files)
            if not has_video:
                missing.append("영상")

            # 워치 확인
            has_watch = any(f.startswith(("ppg", "adxl", "temp", "gsr")) for f in files)
            if not has_watch:
                missing.append("워치")

            # 오디오 확인
            has_audio = any("audio" in f and os.path.getsize(os.path.join(latest, f)) > 100 for f in files)
            if not has_audio:
                missing.append("오디오")

            if missing:
                msg = f"센싱 경고!\n누락: {', '.join(missing)}\n폴더: {os.path.basename(latest)}"
                print(f"[모니터] {msg}")
                notify_popup("센싱 경고", msg)

        except Exception:
            pass

        time.sleep(30)


if __name__ == "__main__":
    import multiprocessing as mp

    try:
        if mp.get_start_method(allow_none=True) != "spawn":
            mp.set_start_method("spawn")
    except RuntimeError:
        pass

    os.makedirs("logs", exist_ok=True)
    faulthandler.enable(open("logs/crash_log.txt", "w"))

    participant_id = get_participant_info()

    save_root = os.path.join(DATA_DIRECTORY, participant_id)
    os.makedirs(save_root, exist_ok=True)

    shutdown_event = threading.Event()

    # 내장 블루투스 끄기 (워치 동글 간섭 방지)
    subprocess.run(["sudo", "-n", "systemctl", "stop", "bluetooth"],
                   capture_output=True, timeout=5)

    threads = [
        threading.Thread(
            target=safe_run_with_retry(run_realsense, "realsense_main", 3,
                                       save_root,
                                       shutdown_event=shutdown_event,
                                       pub=None,
                                       device_serial=RS_MAIN_SERIAL,
                                       is_main_cam=True),
            daemon=True,
        ),
        threading.Thread(
            target=safe_run_with_retry(run_realsense, "realsense_sub", 3,
                                       save_root,
                                       shutdown_event=shutdown_event,
                                       pub=None,
                                       device_serial=RS_SUB_SERIAL,
                                       is_main_cam=False),
            daemon=True,
        ),
        threading.Thread(
            target=safe_run_with_retry(run_rode, "rode", 3,
                                       save_root,
                                       shutdown_event=shutdown_event,
                                       pub=None),
            daemon=True,
        ),
        # 워치는 watch_standalone.py에서 별도 프로세스로 실행 (start.sh가 관리)
        # 데이터 모니터링 스레드
        threading.Thread(
            target=monitor_data,
            args=(save_root, shutdown_event),
            daemon=True,
        ),
    ]

    for t in threads:
        t.start()

    notify_popup("센싱 시작", f"참가자 {participant_id}\n모든 센서가 시작되었습니다.")
    print(f"\n[메인] 참가자 {participant_id} - 모든 센서 시작. Ctrl+C로 종료.")

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[메인] 종료 신호 감지...")
    finally:
        shutdown_event.set()
        for t in threads:
            t.join(timeout=10)
        notify_popup("센싱 종료", f"참가자 {participant_id}\n데이터가 저장되었습니다.")
        print("\n[메인] 모든 스레드 종료 완료.")
