"""Bloom Camera Agent — Meridian Zero property.

Runs on the shop PC (same Wi-Fi as the cameras). Every few seconds it looks at
each camera's video (no sound), counts people, and once an hour-slot is updated
it sends only NUMBERS to Bloom (people per hour, busy minutes, offline minutes,
after-hours movement). Pictures never leave the shop PC: after-hours snapshots
are kept locally for 7 days, then deleted automatically.

Commands:
  python bloom_camera_agent.py --discover   find cameras on the shop Wi-Fi
  python bloom_camera_agent.py --setup      create config.json + the agent token
  python bloom_camera_agent.py --test       one picture per camera, show the count
  python bloom_camera_agent.py              run (keep running, start with Windows)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
CAIRO = ZoneInfo("Africa/Cairo")
log = logging.getLogger("bloom-camera")

DEFAULTS = {
    "agent_id": "shop-pc",
    "supabase_url": "https://nmltdgkzuvymbawrvxbv.supabase.co",
    "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5tbHRkZ2t6dXZ5bWJhd3J2eGJ2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTM2MjA0MDAsImV4cCI6MjEwOTE5NjQwMH0.MKLO14qq-x4IVA44v7xw86eWRnKGORbL_37dudOiBvs",  # public key, same as the app
    "schema": "bloom",
    "token": "",
    "cameras": [],                 # [{"name": "Entrance", "rtsp": "rtsp://user:pass@192.168.1.20:8554/profile1"}]
    "open_hour": 13,               # Bloom opens 13:00
    "close_hour": 20,              # closes 20:00
    "closed_weekdays": [0],        # Monday (Python: Monday = 0)
    "sample_seconds": 5,
    "upload_minutes": 5,
    "photo_dir": "snapshots",
    "keep_days": 7,
    "min_confidence": 0.45,
}


# ---------- detection ----------
class Detector:
    """YOLO (ultralytics) when installed, else OpenCV's built-in person detector."""

    def __init__(self, min_conf: float):
        import cv2  # noqa: F401  (fail early with a clear message)
        self.min_conf = min_conf
        self.kind = "hog"
        try:
            from ultralytics import YOLO  # type: ignore
            self.model = YOLO("yolov8n.pt")
            self.kind = "yolo"
        except Exception:
            import cv2
            self.hog = cv2.HOGDescriptor()
            self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        log.info("detector: %s", self.kind)

    def count(self, frame) -> int:
        if self.kind == "yolo":
            res = self.model(frame, classes=[0], conf=self.min_conf, verbose=False)
            return int(len(res[0].boxes)) if res else 0
        import cv2
        h, w = frame.shape[:2]
        scale = 640 / max(w, 1)
        small = cv2.resize(frame, (640, int(h * scale))) if scale < 1 else frame
        rects, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        return int(sum(1 for wgt in weights if float(wgt) >= self.min_conf))


# ---------- hourly aggregation (pure, unit-tested) ----------
@dataclass
class HourStats:
    samples: int = 0
    total: int = 0
    people_max: int = 0
    busy: set = field(default_factory=set)        # minutes with >=1 person
    offline: set = field(default_factory=set)     # minutes the camera could not be read
    after_hours: set = field(default_factory=set) # minutes with a person while closed

    def row(self, day: str, hour: int, camera: str) -> dict:
        return {
            "day": day, "hour": hour, "camera": camera,
            "people_max": min(self.people_max, 50),
            "people_avg": round(self.total / self.samples, 2) if self.samples else 0,
            "busy_minutes": min(len(self.busy), 60),
            "offline_minutes": min(len(self.offline), 60),
            "after_hours_events": min(len(self.after_hours), 60),
        }


def is_open(t: dt.datetime, cfg: dict) -> bool:
    return t.weekday() not in cfg["closed_weekdays"] and cfg["open_hour"] <= t.hour < cfg["close_hour"]


class Ledger:
    """Thread-safe store of HourStats keyed by (day, hour, camera)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.stats: dict[tuple[str, int, str], HourStats] = {}

    def add(self, camera: str, t: dt.datetime, people: int | None) -> bool:
        """Record one sample. Returns True when it is after-hours movement."""
        key = (t.date().isoformat(), t.hour, camera)
        with self.lock:
            s = self.stats.setdefault(key, HourStats())
            if people is None:
                s.offline.add(t.minute)
                return False
            s.samples += 1
            s.total += people
            s.people_max = max(s.people_max, people)
            if people:
                s.busy.add(t.minute)
                if not is_open(t, self.cfg):
                    new = t.minute not in s.after_hours
                    s.after_hours.add(t.minute)
                    return new
        return False

    def rows(self, since: dt.datetime) -> list[dict]:
        with self.lock:
            out = []
            for (day, hour, cam), s in self.stats.items():
                start = dt.datetime.fromisoformat(day).replace(hour=hour, tzinfo=CAIRO)
                if start >= since.replace(minute=0, second=0, microsecond=0):
                    out.append(s.row(day, hour, cam))
            return out

    def prune(self, before: dt.datetime) -> None:
        with self.lock:
            for k in [k for k in self.stats
                      if dt.datetime.fromisoformat(k[0]).replace(hour=k[1], tzinfo=CAIRO) < before]:
                del self.stats[k]


# ---------- upload ----------
def upload(cfg: dict, rows: list[dict]) -> int:
    if not rows:
        return 0
    body = json.dumps({"p_agent": cfg["agent_id"], "p_token": cfg["token"], "p_rows": rows}).encode()
    req = urllib.request.Request(
        cfg["supabase_url"].rstrip("/") + "/rest/v1/rpc/camera_ingest", data=body, method="POST",
        headers={"apikey": cfg["anon_key"], "Authorization": "Bearer " + cfg["anon_key"],
                 "Content-Type": "application/json",
                 "Accept-Profile": cfg["schema"], "Content-Profile": cfg["schema"]})
    with urllib.request.urlopen(req, timeout=20) as r:
        return int(json.loads(r.read() or b"0"))


# ---------- photos (local only) ----------
def save_snapshot(cfg: dict, camera: str, frame, t: dt.datetime) -> None:
    import cv2
    folder = HERE / cfg["photo_dir"] / t.date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in camera if c.isalnum() or c in "-_") or "cam"
    cv2.imwrite(str(folder / f"{t:%H%M%S}_{safe}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])


def clean_photos(cfg: dict, now: dt.datetime) -> None:
    root = HERE / cfg["photo_dir"]
    if not root.exists():
        return
    limit = (now - dt.timedelta(days=cfg["keep_days"])).date()
    for d in root.iterdir():
        try:
            if d.is_dir() and dt.date.fromisoformat(d.name) < limit:
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()
        except ValueError:
            continue


# ---------- camera loop ----------
def watch(cam: dict, cfg: dict, det: Detector, ledger: Ledger, stop: threading.Event) -> None:
    import cv2
    name, url = cam["name"], cam["rtsp"]
    cap = None
    while not stop.is_set():
        t = dt.datetime.now(CAIRO)
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        ok, frame = (cap.read() if cap is not None else (False, None))
        if not ok or frame is None:
            ledger.add(name, t, None)
            if cap is not None:
                cap.release()
            cap = None
            stop.wait(10)
            continue
        try:
            n = det.count(frame)
        except Exception as e:  # never die on one bad frame
            log.warning("%s: detect failed: %s", name, e)
            n = 0
        if ledger.add(name, t, n):
            save_snapshot(cfg, name, frame, t)
            log.info("%s: movement after hours (%d person)", name, n)
        # drain buffered frames so the next read is fresh
        end = time.time() + cfg["sample_seconds"]
        while time.time() < end and not stop.is_set():
            if cap is not None:
                cap.grab()
            time.sleep(0.2)


def run(cfg: dict) -> None:
    det = Detector(cfg["min_confidence"])
    ledger, stop = Ledger(cfg), threading.Event()
    for cam in cfg["cameras"]:
        threading.Thread(target=watch, args=(cam, cfg, det, ledger, stop), daemon=True).start()
    log.info("watching %d camera(s)", len(cfg["cameras"]))
    try:
        while True:
            time.sleep(cfg["upload_minutes"] * 60)
            now = dt.datetime.now(CAIRO)
            try:
                n = upload(cfg, ledger.rows(now - dt.timedelta(hours=1)))
                log.info("sent %d hourly row(s)", n)
                ledger.prune(now - dt.timedelta(hours=3))
            except Exception as e:
                log.warning("upload failed, will retry: %s", e)
            clean_photos(cfg, now)
    except KeyboardInterrupt:
        stop.set()


# ---------- setup helpers ----------
def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg


def discover() -> list[str]:
    """Find devices on the local /24 that answer on RTSP ports 554 or 8554."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        base = ".".join(s.getsockname()[0].split(".")[:3])
    finally:
        s.close()

    def probe(ip: str) -> str | None:
        for port in (554, 8554):
            try:
                with socket.create_connection((ip, port), timeout=0.4):
                    return f"{ip}:{port}"
            except OSError:
                pass
        return None

    with cf.ThreadPoolExecutor(64) as ex:
        found = [r for r in ex.map(probe, (f"{base}.{i}" for i in range(1, 255))) if r]
    return found


def setup() -> None:
    cfg = load_cfg()
    if not cfg["token"]:
        cfg["token"] = secrets.token_urlsafe(32)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print("config.json is ready. Add your cameras (name + rtsp address) inside it.")
    print("Give Claude ONLY this fingerprint to register the PC (the token stays here):")
    print("  agent:", cfg["agent_id"])
    print("  fingerprint:", hashlib.sha256(cfg["token"].encode()).hexdigest())


def test(cfg: dict) -> None:
    import cv2
    det = Detector(cfg["min_confidence"])
    for cam in cfg["cameras"]:
        cap = cv2.VideoCapture(cam["rtsp"], cv2.CAP_FFMPEG)
        ok, frame = cap.read()
        cap.release()
        print(f"{cam['name']}: " + (f"OK, {det.count(frame)} person(s) seen" if ok else "NOT REACHABLE"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bloom Camera Agent")
    p.add_argument("--discover", action="store_true")
    p.add_argument("--setup", action="store_true")
    p.add_argument("--test", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(HERE / "agent.log", encoding="utf-8")])
    if a.discover:
        found = discover()
        print("\n".join(found) if found else "No cameras answered on ports 554/8554.")
        return 0
    if a.setup:
        setup()
        return 0
    cfg = load_cfg()
    if not cfg["cameras"] or not cfg["token"] or not cfg["anon_key"]:
        print("Run --setup first and add the cameras to config.json.")
        return 1
    if a.test:
        test(cfg)
        return 0
    run(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
