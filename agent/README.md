# Bloom Camera Agent (Meridian Zero)

Runs on the **shop PC** (same Wi-Fi as the Tenda cameras). Video only, no sound.
Only numbers leave the shop (people per hour, busy/offline minutes, after-hours movement).
After-hours snapshots stay on the shop PC for 7 days, then delete themselves.

| Step | Command (Windows Terminal, normal user — not admin) |
|---|---|
| 1 Install | `pip install -r requirements.txt` |
| 2 Find cameras | `python bloom_camera_agent.py --discover` |
| 3 Create config | `python bloom_camera_agent.py --setup` → give Claude the **fingerprint** only |
| 4 Add cameras | In `config.json`: `{"name": "Entrance", "rtsp": "rtsp://USER:PASS@IP:8554/profile1"}` — the owner types the camera password |
| 5 Test | `python bloom_camera_agent.py --test` |
| 6 Run | `start-bloom-cameras.bat` (Task Scheduler: at log on) |

Tenda RTSP paths seen in the field: `:8554/profile0` (main), `:8554/profile1` (light), or `/tenda`.
