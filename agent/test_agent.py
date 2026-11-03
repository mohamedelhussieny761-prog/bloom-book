import datetime as dt, sys, unittest
sys.path.insert(0, __import__("os").path.dirname(__file__))
import bloom_camera_agent as A
C = A.CAIRO
cfg = dict(A.DEFAULTS)

class T(unittest.TestCase):
    def test_open_hours(self):
        self.assertTrue(A.is_open(dt.datetime(2026,11,4,13,0,tzinfo=C), cfg))   # Wed 13:00
        self.assertFalse(A.is_open(dt.datetime(2026,11,4,20,0,tzinfo=C), cfg))  # closing
        self.assertFalse(A.is_open(dt.datetime(2026,11,9,15,0,tzinfo=C), cfg))  # Monday
    def test_hourly_row(self):
        L = A.Ledger(cfg); t = dt.datetime(2026,11,4,14,5,tzinfo=C)
        L.add("Entrance", t, 2); L.add("Entrance", t.replace(minute=6), 0); L.add("Entrance", t.replace(minute=7), None)
        r = L.rows(t)[0]
        self.assertEqual((r["people_max"], r["people_avg"], r["busy_minutes"], r["offline_minutes"], r["after_hours_events"]), (2, 1.0, 1, 1, 0))
    def test_after_hours_once_per_minute(self):
        L = A.Ledger(cfg); t = dt.datetime(2026,11,4,23,10,tzinfo=C)
        self.assertTrue(L.add("Hall", t, 1)); self.assertFalse(L.add("Hall", t.replace(second=30), 1))
        self.assertEqual(L.rows(t)[0]["after_hours_events"], 1)
    def test_prune(self):
        L = A.Ledger(cfg); t = dt.datetime(2026,11,4,10,0,tzinfo=C)
        L.add("Hall", t, 1); L.prune(t + dt.timedelta(hours=4)); self.assertEqual(L.rows(t - dt.timedelta(days=1)), [])
    def test_hog_runs(self):
        import numpy as np
        d = A.Detector(0.45); self.assertEqual(d.count(np.zeros((480,640,3), dtype="uint8")), 0)

if __name__ == "__main__": unittest.main()
