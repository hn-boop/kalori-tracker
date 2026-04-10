#!/usr/bin/env python3
"""
Garmin lokaalne sync server.
Käivita: python sync_server.py
Seejärel vajuta rakenduses "Sync Garmin" nuppu.
"""
import json
import os
import sys
import threading
from datetime import date, timedelta, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import urllib.request
import urllib.error

PORT = 5001
RAILWAY_URL = "https://web-production-9ade8.up.railway.app"
CONFIG_FILE = Path(__file__).parent / "garmin_config.json"
TOKEN_DIR = Path(__file__).parent / ".garmin_tokens"
DAYS_TO_FETCH = 90  # mitu päeva tagasi laadida

# ── Config ─────────────────────────────────────────────────────────────────────

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

# ── Garmin sync ────────────────────────────────────────────────────────────────

def do_garmin_sync(log):
    cfg = load_config()
    email = cfg.get("email", "")
    password = cfg.get("password", "")

    if not email or not password:
        return False, "Garmin email/parool pole seadistatud. Lisa garmin_config.json faili."

    try:
        import garminconnect
    except ImportError:
        return False, "garminconnect pole installitud. Käivita: pip install garminconnect"

    # Autentimine koos token-caching'uga
    log("🔑 Garmin autentimine (kasutan salvestatud tokeneid kui võimalik)...")
    TOKEN_DIR.mkdir(exist_ok=True)
    try:
        garmin = garminconnect.Garmin(
            email, password,
            is_cn=False,
            tokenstore=str(TOKEN_DIR)
        )
        garmin.login()
        log("✅ Garmin sisselogimine õnnestus")
    except Exception as e:
        return False, f"Garmin login ebaõnnestus: {e}"

    today = date.today()
    start = today - timedelta(days=DAYS_TO_FETCH)

    uds_data = []
    sleep_data = []
    readiness_data = []

    log(f"📥 Laen andmeid: {start} → {today} ({DAYS_TO_FETCH} päeva)...")

    current = start
    while current <= today:
        ds = current.strftime("%Y-%m-%d")

        # UDS (sammud, kalorid, HR, stress)
        try:
            stats = garmin.get_stats(ds)
            if stats:
                uds_data.append({
                    "calendarDate": ds,
                    "totalKilocalories": stats.get("totalKilocalories"),
                    "activeKilocalories": stats.get("activeKilocalories"),
                    "bmrKilocalories": stats.get("bmrKilocalories"),
                    "totalSteps": stats.get("totalSteps"),
                    "restingHeartRate": stats.get("restingHeartRate"),
                    "averageStressLevel": stats.get("averageStressLevel"),
                })
        except Exception as e:
            log(f"  ⚠️  UDS {ds}: {e}")

        # Uni
        try:
            sleep = garmin.get_sleep_data(ds)
            if sleep:
                dto = sleep.get("dailySleepDTO") or {}
                scores = sleep.get("sleepScores") or {}
                if dto.get("calendarDate"):
                    sleep_data.append({
                        "calendarDate": ds,
                        "deepSleepSeconds": dto.get("deepSleepSeconds"),
                        "lightSleepSeconds": dto.get("lightSleepSeconds"),
                        "remSleepSeconds": dto.get("remSleepSeconds"),
                        "awakeSleepSeconds": dto.get("awakeSleepSeconds"),
                        "overallScore": (scores.get("overall") or {}).get("value"),
                        "recoveryScore": (scores.get("recovery") or {}).get("value"),
                        "durationScore": (scores.get("duration") or {}).get("value"),
                    })
        except Exception as e:
            log(f"  ⚠️  Uni {ds}: {e}")

        # Training Readiness
        try:
            rdns = garmin.get_training_readiness(ds)
            if rdns:
                r = rdns[0] if isinstance(rdns, list) and rdns else rdns
                if isinstance(r, dict) and r.get("calendarDate"):
                    readiness_data.append({
                        "calendarDate": ds,
                        "score": r.get("score"),
                        "level": r.get("level"),
                        "hrvFactorPercent": r.get("hrvFactorPercent"),
                        "hrvWeeklyAverage": r.get("hrvWeeklyAverage"),
                    })
        except Exception as e:
            log(f"  ⚠️  Readiness {ds}: {e}")

        current += timedelta(days=1)

    log(f"📊 UDS: {len(uds_data)}p · Uni: {len(sleep_data)}p · Readiness: {len(readiness_data)}p")

    # Laeme Railway serverist olemasolevad andmed
    log("☁️  Laen serverist olemasolevad andmed...")
    server_data = {}
    try:
        req = urllib.request.Request(f"{RAILWAY_URL}/sync")
        with urllib.request.urlopen(req, timeout=15) as resp:
            server_data = json.loads(resp.read())
    except Exception as e:
        log(f"  ⚠️  Serveri andmete lugemine: {e} (jätkan tühja andmestikuga)")

    # Merge: serveri garmin_data + uued andmed
    existing = server_data.get("garmin_data") or {}

    def merge_by_date(existing_list, new_list):
        m = {}
        for r in (existing_list or []):
            if r.get("calendarDate"):
                m[r["calendarDate"]] = r
        for r in new_list:
            if r.get("calendarDate"):
                m[r["calendarDate"]] = r  # uued kirjutavad üle
        return sorted(m.values(), key=lambda x: x["calendarDate"])

    merged_uds = merge_by_date(existing.get("uds"), uds_data)
    merged_sleep = merge_by_date(existing.get("sleep"), sleep_data)
    merged_readiness = merge_by_date(existing.get("readiness"), readiness_data)

    # Ehita upload payload — säilita kõik olemasolevad võtmed (toitumine jm)
    upload_payload = dict(server_data)  # kõik olemasolevad andmed
    upload_payload["garmin_data"] = {
        "exportedAt": datetime.now().isoformat(),
        "uds": merged_uds,
        "sleep": merged_sleep,
        "readiness": merged_readiness,
    }

    log(f"☁️  Laen üles serverisse (UDS: {len(merged_uds)}, Uni: {len(merged_sleep)}, Readiness: {len(merged_readiness)})...")
    try:
        body = json.dumps(upload_payload).encode("utf-8")
        req = urllib.request.Request(
            f"{RAILWAY_URL}/sync",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
        log(f"✅ Server: HTTP {status}")
        return True, f"Sünkroniseeritud! UDS: {len(merged_uds)}p · Uni: {len(merged_sleep)}p · Readiness: {len(merged_readiness)}p"
    except Exception as e:
        return False, f"Serveri upload ebaõnnestus: {e}"

# ── HTTP server ────────────────────────────────────────────────────────────────

sync_lock = threading.Lock()
last_result = {"ok": None, "msg": "Pole sünkroniseeritud", "running": False}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # vaigista vaikimisi logid

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            self.wfile.write(json.dumps(last_result).encode())
        elif self.path == "/sync":
            self._run_sync()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/sync":
            self._run_sync()
        else:
            self.send_response(404)
            self.end_headers()

    def _run_sync(self):
        global last_result

        if last_result["running"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": None, "msg": "Sync juba käib...", "running": True}).encode())
            return

        logs = []
        def log(msg):
            print(msg)
            logs.append(msg)

        def run():
            global last_result
            last_result = {"ok": None, "msg": "Sync käib...", "running": True, "logs": []}
            ok, msg = do_garmin_sync(log)
            last_result = {"ok": ok, "msg": msg, "running": False, "logs": logs}

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_cors()
        self.end_headers()
        self.wfile.write(json.dumps({"ok": None, "msg": "Sync alustatud...", "running": True}).encode())

# ── Config setup ───────────────────────────────────────────────────────────────

def setup_config():
    cfg = load_config()
    changed = False

    if not cfg.get("email"):
        cfg["email"] = input("Garmin Connect email: ").strip()
        changed = True
    if not cfg.get("password"):
        import getpass
        cfg["password"] = getpass.getpass("Garmin Connect parool: ")
        changed = True

    if changed:
        save_config(cfg)
        print(f"✅ Seaded salvestatud: {CONFIG_FILE}")

    return cfg

# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("Garmin Sync Server")
    print("=" * 50)

    # Kontrolli garminconnect
    try:
        import garminconnect
        print(f"✅ garminconnect installitud")
    except ImportError:
        print("❌ garminconnect pole installitud!")
        print("   Käivita: pip install garminconnect")
        sys.exit(1)

    # Seadistamine
    setup_config()

    # Käivita server
    server = HTTPServer(("localhost", PORT), Handler)
    print(f"\n🚀 Server käib: http://localhost:{PORT}")
    print(f"   /sync   — käivita Garmin sync")
    print(f"   /status — sync staatus")
    print("\nVajuta rakenduses 'Sync Garmin' nuppu!")
    print("Lõpetamiseks: Ctrl+C\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server peatatud")
