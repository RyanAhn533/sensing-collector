
import zmq
import json
import base64
import zlib
import cv2
import numpy as np

class RealtimePublisher:
    """
    ZeroMQ PUB helper.
    Topics:
      - "frame":     {"jpg_b64": ...}
      - "audio_rms": {"ts": ms, "rms": float}
      - "audio_chunk": {"ts": ms, "sr": 48000, "mono_b64": base64(zlib.compress(float32_bytes))}
      - "eda":  {"ts": ms, "imp_real": float}
      - "ppg":  {"ts": ms, "d1": float, "d2": float}
      - "temp": {"ts": ms, "skin_c": float}
    """
    def __init__(self, bind="tcp://127.0.0.1:5556", high_watermark: int = 1):
        ctx = zmq.Context.instance()
        self.sock = ctx.socket(zmq.PUB)

        self.sock.setsockopt(zmq.SNDHWM, int(high_watermark))
        self.sock.setsockopt(zmq.LINGER, 0)

        # (옵션) PUB에서도 “최신만” 성격을 더 강하게 하고 싶으면:
        # self.sock.setsockopt(zmq.CONFLATE, 1)

        self.sock.bind(bind)

    def _send_json(self, topic: str, payload: dict):
        msg = json.dumps(payload).encode("utf-8")
        try:
            self.sock.send_multipart([topic.encode(), msg], flags=zmq.DONTWAIT)
        except zmq.Again:
            # 막히면 드랍
            return

    # ---------- Video ----------
    def send_frame(self, frame_bgr, quality: int = 70, size=(640, 480)):
        if frame_bgr is None:
            return
        if size:
            frame_bgr = cv2.resize(frame_bgr, size)
        ok, jpg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            return
        b64 = base64.b64encode(jpg.tobytes()).decode("ascii")
        self._send_json("frame", {"jpg_b64": b64})

    # ---------- Audio ----------
    def send_audio_rms(self, ts_ms: int, rms: float):
        self._send_json("audio_rms", {"ts": int(ts_ms), "rms": float(rms)})

    def send_audio_chunk(self, ts_ms: int, mono_float32: np.ndarray, sr: int):
        """mono_float32: 1-D float32 numpy array in [-1,1]"""
        if mono_float32 is None or mono_float32.size == 0:
            return
        if mono_float32.dtype != np.float32:
            mono_float32 = mono_float32.astype(np.float32, copy=False)
        raw = mono_float32.tobytes(order="C")
        comp = zlib.compress(raw, level=6)
        b64 = base64.b64encode(comp).decode("ascii")
        self._send_json("audio_chunk", {"ts": int(ts_ms), "sr": int(sr), "mono_b64": b64})

    # ---------- Bio signals ----------
    def send_eda(self, ts_ms: int, imp_real_ohm: float):
        self._send_json("eda", {"ts": int(ts_ms), "imp_real": float(imp_real_ohm)})

    def send_ppg(self, ts_ms: int, d1: float, d2: float):
        self._send_json("ppg", {"ts": int(ts_ms), "d1": float(d1), "d2": float(d2)})

    def send_temp(self, ts_ms: int, skin_c: float):
        self._send_json("temp", {"ts": int(ts_ms), "skin_c": float(skin_c)})