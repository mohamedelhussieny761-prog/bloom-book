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
        self.lock = threading.Lock()            # one detection at a time across cameras
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
        with self.lock:
            return self._count(frame)

    def _count(self, frame) -> int:
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
            "people_avg": min(round(self.total / self.samples, 2), 50) if self.samples else 0,
            "busy_minutes": min(len(self.busy), 60),
            "offline_minutes": min(len(self.offline), 60),
            "after_hours_events": min(len(self.after_hours), 60),
        }


def is_open(t: dt.datetime, cfg: dict) -> bool:
    return t.weekday() not in cfg["closed_weekdays"] and cfg["open_hour"] <= t.hour < cfg["close_hour"]


class Ledger:
    """Thread-safe store of HourStats keyed by (day, hour, camera).

    Every changed hour is marked pending until the server confirms it, and the
    whole ledger is saved to state.json so a restart never loses an hour.
    """

    def __init__(self, cfg: dict, state: Path | None = None):
        self.cfg = cfg
        self.state = state
        self.lock = threading.Lock()
        self.stats: dict[tuple[str, int, str], HourStats] = {}
        self.pending: set[tuple[str, int, str]] = set()
        self.rev: dict[tuple[str, int, str], int] = {}   # bumps on every change
        if state and state.exists():
            self._load()

    def add(self, camera: str, t: dt.datetime, people: int | None) -> bool:
        """Record one sample. Returns True when it is new after-hours movement."""
        key = (t.date().isoformat(), t.hour, camera)
        with self.lock:
            s = self.stats.setdefault(key, HourStats())
            self.pending.add(key)
            self.rev[key] = self.rev.get(key, 0) + 1
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

    def take_pending(self) -> tuple[list[dict], dict]:
        """Rows for every hour not yet confirmed (oldest first, max 200) + their revisions."""
        with self.lock:
            keys = sorted(self.pending)[:200]
            return [self.stats[k].row(*k) for k in keys], {k: self.rev.get(k, 0) for k in keys}

    def confirm(self, sent: dict) -> None:
        """Clear only hours that did not change while the upload was in flight."""
        with self.lock:
            for k, r in sent.items():
                if self.rev.get(k, 0) == r:
                    self.pending.discard(k)

    def rows(self, since: dt.datetime) -> list[dict]:
        with self.lock:
            out = []
            for (day, hour, cam), s in self.stats.items():
                start = dt.datetime.fromisoformat(day).replace(hour=hour, tzinfo=CAIRO)
                if start >= since.replace(minute=0, second=0, microsecond=0):
                    out.append(s.row(day, hour, cam))
            return out

    def prune(self, before: dt.datetime) -> None:
        """Forget old hours, but never one still waiting to be sent."""
        with self.lock:
            for k in [k for k in self.stats if k not in self.pending
                      and dt.datetime.fromisoformat(k[0]).replace(hour=k[1], tzinfo=CAIRO) < before]:
                del self.stats[k]
                self.rev.pop(k, None)

    def save(self) -> None:
        if not self.state:
            return
        with self.lock:
            data = {"|".join(map(str, k)): {"samples": s.samples, "total": s.total, "people_max": s.people_max,
                                            "busy": sorted(s.busy), "offline": sorted(s.offline),
                                            "after_hours": sorted(s.after_hours), "pending": k in self.pending}
                    for k, s in self.stats.items()}
        tmp = self.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.state)

    def _load(self) -> None:
        try:
            data = json.loads(self.state.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("could not read %s: %s", self.state, e)
            return
        for k, v in data.items():
            day, hour, cam = k.split("|", 2)
            key = (day, int(hour), cam)
            self.stats[key] = HourStats(v["samples"], v["total"], v["people_max"], set(v["busy"]),
                                        set(v["offline"]), set(v["after_hours"]))
            if v.get("pending"):
                self.pending.add(key)


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
    tag = hashlib.sha1(camera.encode()).hexdigest()[:6]   # keeps "Hall 1" and "Hall1" apart
    path = folder / f"{t:%H%M%S}_{safe}_{tag}.jpg"
    if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 80]):
        raise OSError(f"could not write {path}")


def clean_photos(cfg: dict, now: dt.datetime) -> None:
    """Delete each snapshot older than keep_days (exactly), then empty day folders."""
    root = HERE / cfg["photo_dir"]
    if not root.exists():
        return
    limit = now.timestamp() - cfg["keep_days"] * 86400
    for d in root.iterdir():
        if not d.is_dir():
            continue
        for f in d.iterdir():
            try:
                if f.stat().st_mtime < limit:
                    f.unlink()
            except OSError as e:
                log.warning("could not remove %s: %s", f, e)
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass


# ---------- camera loop ----------
class Reader:
    """Reads one camera continuously so the latest frame is always fresh."""

    def __init__(self, url: str, stop: threading.Event):
        self.url, self.stop = url, stop
        self.lock = threading.Lock()
        self.frame, self.at = None, 0.0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        import cv2
        while not self.stop.is_set():
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            while cap.isOpened() and not self.stop.is_set():
                ok, frame = cap.read()          # blocks at the camera's own frame rate
                if not ok or frame is None:
                    break
                with self.lock:
                    self.frame, self.at = frame, time.time()
            cap.release()
            self.stop.wait(10)                  # camera dropped: try again in 10 s

    def latest(self, max_age: float = 3.0):
        with self.lock:
            return self.frame if self.frame is not None and time.time() - self.at <= max_age else None


def watch(cam: dict, cfg: dict, det: Detector, ledger: Ledger, stop: threading.Event) -> None:
    name = cam["name"]
    reader = Reader(cam["rtsp"], stop)
    stop.wait(5)                                # give the stream a moment to start
    while not stop.is_set():
        t = dt.datetime.now(CAIRO)
        frame = reader.latest()
        if frame is None:
            ledger.add(name, t, None)
        else:
            try:
                n = det.count(frame)
            except Exception as e:              # never die on one bad frame
                log.warning("%s: detect failed: %s", name, e)
                ledger.add(name, t, None)       # counted as unavailable, not as an empty room
                stop.wait(cfg["sample_seconds"])
                continue
            if ledger.add(name, t, n):
                log.info("%s: movement after hours (%d person)", name, n)
                try:
                    save_snapshot(cfg, name, frame, t)
                except Exception as e:          # full disk etc. must not stop the camera
                    log.warning("%s: could not save snapshot: %s", name, e)
        stop.wait(cfg["sample_seconds"])


def run(cfg: dict) -> None:
    det = Detector(cfg["min_confidence"])
    ledger, stop = Ledger(cfg, HERE / "state.json"), threading.Event()
    for cam in cfg["cameras"]:
        threading.Thread(target=watch, args=(cam, cfg, det, ledger, stop), daemon=True).start()
    log.info("watching %d camera(s)", len(cfg["cameras"]))
    last_upload = 0.0
    try:
        while True:
            time.sleep(60)
            safe_save(ledger)                   # survive restarts
            if time.time() - last_upload < cfg["upload_minutes"] * 60:
                continue
            last_upload = time.time()
            now = dt.datetime.now(CAIRO)
            rows, keys = ledger.take_pending()
            try:
                n = upload(cfg, rows)
                ledger.confirm(keys)
                log.info("sent %d hourly row(s)", n)
            except Exception as e:
                log.warning("upload failed, will retry: %s", e)
            ledger.prune(now - dt.timedelta(hours=3))
            try:
                clean_photos(cfg, now)          # first free space, then save
            except Exception as e:              # unreadable folder etc.: keep monitoring
                log.warning("photo cleanup failed: %s", e)
            safe_save(ledger)
    except KeyboardInterrupt:
        stop.set()
        safe_save(ledger)


def safe_save(ledger: Ledger) -> None:
    try:
        ledger.save()
    except Exception as e:                      # full disk etc.: keep running, retry next minute
        log.warning("could not save state: %s", e)


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
    names = [c.get("name", "") for c in cfg["cameras"]]
    bad = [n for n in names if not 1 <= len(n) <= 40]
    if bad or len(set(names)) != len(names):
        print("Each camera needs its own name, 1 to 40 characters.")
        return 1
    if any(not str(c.get("rtsp", "")).startswith("rtsp://") for c in cfg["cameras"]):
        print("Each camera needs its rtsp:// address in config.json.")
        return 1
    if a.test:
        test(cfg)
        return 0
    run(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
