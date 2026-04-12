#!/usr/bin/env python3
"""
Garmin lokaalne sync server.

Installimine (üks kord):
    pip install playwright
    playwright install chromium

Käivita:
    python sync_server.py

Seejärel vajuta rakenduses "SYNC" nuppu — brauser avaneb automaatselt.
"""
import json
import sys
import threading
import time
from datetime import date, timedelta, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.request

PORT = 5001
RAILWAY_URL = "https://web-production-9ade8.up.railway.app"
DAYS_TO_FETCH = 30

# ── Garmin andmete laadimine brauseri kaudu ───────────────────────────────────

def fetch_day(page, display_name, ds):
    """Tõmbab ühe päeva UDS, uni ja readiness andmed brauseri fetch() kaudu."""
    uds, sleep, readiness = None, None, None

    # UDS (kalorid, sammud, HR, stress)
    try:
        r = page.evaluate("""async () => {
            const res = await fetch('https://connectapi.garmin.com/usersummary-service/usersummary/daily/""" + display_name + """?calendarDate=""" + ds + """', {
                headers: {'NK': 'NT', 'Accept': 'application/json'}
            });
            return res.ok ? res.json() : null;
        }""")
        if r and r.get("totalKilocalories"):
            uds = {
                "calendarDate": ds,
                "totalKilocalories": r.get("totalKilocalories"),
                "activeKilocalories": r.get("activeKilocalories"),
                "bmrKilocalories": r.get("bmrKilocalories"),
                "totalSteps": r.get("totalSteps"),
                "restingHeartRate": r.get("restingHeartRate"),
                "averageStressLevel": r.get("averageStressLevel"),
            }
    except Exception:
        pass

    # Uni
    try:
        r = page.evaluate("""async () => {
            const res = await fetch('https://connectapi.garmin.com/wellness-service/wellness/dailySleepData/""" + ds + """', {
                headers: {'NK': 'NT', 'Accept': 'application/json'}
            });
            return res.ok ? res.json() : null;
        }""")
        if r:
            dto = r.get("dailySleepDTO") or {}
            scores = r.get("sleepScores") or {}
            if dto.get("calendarDate") or dto.get("sleepTimeSeconds"):
                sleep = {
                    "calendarDate": ds,
                    "deepSleepSeconds": dto.get("deepSleepSeconds"),
                    "lightSleepSeconds": dto.get("lightSleepSeconds"),
                    "remSleepSeconds": dto.get("remSleepSeconds"),
                    "awakeSleepSeconds": dto.get("awakeSleepSeconds"),
                    "overallScore": (scores.get("overall") or {}).get("value"),
                    "recoveryScore": (scores.get("recovery") or {}).get("value"),
                    "durationScore": (scores.get("duration") or {}).get("value"),
                }
    except Exception:
        pass

    # Training Readiness
    try:
        r = page.evaluate("""async () => {
            const res = await fetch('https://connectapi.garmin.com/metrics-service/metrics/trainingreadiness/""" + ds + """', {
                headers: {'NK': 'NT', 'Accept': 'application/json'}
            });
            return res.ok ? res.json() : null;
        }""")
        if r:
            items = r if isinstance(r, list) else [r]
            for item in items:
                if isinstance(item, dict) and item.get("score"):
                    readiness = {
                        "calendarDate": item.get("calendarDate", ds),
                        "score": item.get("score"),
                        "level": item.get("level"),
                        "hrvFactorPercent": item.get("hrvFactorPercent"),
                        "hrvWeeklyAverage": item.get("hrvWeeklyAverage"),
                    }
                    break
    except Exception:
        pass

    return uds, sleep, readiness


def do_garmin_sync(log, set_msg):
    """Playwright brauser → käsitsi login → andmed alla → Railway üles."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, (
            "Playwright pole installitud! Käivita terminalis:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )

    log("🌐 Avan Garmin Connect brauseris...")
    set_msg("🌐 Brauser avatud — logi sisse Garmin Connect'is")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://connect.garmin.com/signin")

        log("⏳ Oota kuni oled sisse loginud (max 3 minutit)...")

        # Oota sisselogimist — URL muutub pärast edukat loginit
        deadline = time.time() + 180
        while time.time() < deadline:
            url = page.url
            if "connect.garmin.com" in url and "signin" not in url and "sso" not in url:
                break
            time.sleep(1)
        else:
            browser.close()
            return False, "Sisselogimine aegus (3 minutit möödas)"

        log("✅ Sisselogimine tuvastatud!")
        set_msg("✅ Sisse logitud — laen andmeid...")
        time.sleep(2)  # anna leheküljele aega täielikult laadida

        # Leia displayName
        log("👤 Laen kasutajaprofiili...")
        display_name = None
        try:
            result = page.evaluate("""async () => {
                const res = await fetch('https://connectapi.garmin.com/userprofile-service/socialProfile', {
                    headers: {'NK': 'NT', 'Accept': 'application/json'}
                });
                return res.json();
            }""")
            if result:
                display_name = result.get("displayName") or result.get("userName")
        except Exception as e:
            log(f"⚠️  Profiili viga: {e}")

        if not display_name:
            browser.close()
            return False, "Kasutajanime ei leitud — proovi uuesti"

        log(f"👤 Kasutaja: {display_name}")

        # Laadi andmed päev-päeva haaval
        today = date.today()
        start = today - timedelta(days=DAYS_TO_FETCH)
        uds_data, sleep_data, readiness_data = [], [], []

        log(f"📥 Laen andmeid: {start} → {today}...")
        current = start
        total = (today - start).days + 1
        done = 0

        while current <= today:
            ds = current.strftime("%Y-%m-%d")
            uds, sleep, readiness = fetch_day(page, display_name, ds)
            if uds:
                uds_data.append(uds)
            if sleep:
                sleep_data.append(sleep)
            if readiness:
                readiness_data.append(readiness)
            done += 1
            set_msg(f"📥 Laen andmeid... {done}/{total} päeva")
            current += timedelta(days=1)

        browser.close()

    log(f"📊 UDS: {len(uds_data)}p · Uni: {len(sleep_data)}p · Readiness: {len(readiness_data)}p")

    if not uds_data and not sleep_data:
        return False, "Andmeid ei saadud (0 kirjet) — proovi uuesti"

    # Laadi Railway serverist olemasolevad andmed
    log("☁️  Laen serverist olemasolevad andmed...")
    set_msg("☁️  Laadimine serverisse...")
    server_data = {}
    try:
        with urllib.request.urlopen(f"{RAILWAY_URL}/sync", timeout=15) as r:
            server_data = json.loads(r.read())
    except Exception as e:
        log(f"  ⚠️  Serveri lugemine: {e} (jätkan tühja andmestikuga)")

    def merge_by_date(existing, new):
        m = {r["calendarDate"]: r for r in (existing or []) if r.get("calendarDate")}
        m.update({r["calendarDate"]: r for r in new if r.get("calendarDate")})
        return sorted(m.values(), key=lambda x: x["calendarDate"])

    existing = server_data.get("garmin_data") or {}
    merged_uds = merge_by_date(existing.get("uds"), uds_data)
    merged_sleep = merge_by_date(existing.get("sleep"), sleep_data)
    merged_readiness = merge_by_date(existing.get("readiness"), readiness_data)

    upload = dict(server_data)
    upload["garmin_data"] = {
        "exportedAt": datetime.now().isoformat(),
        "uds": merged_uds,
        "sleep": merged_sleep,
        "readiness": merged_readiness,
    }

    log(f"☁️  Laen üles (UDS: {len(merged_uds)}, Uni: {len(merged_sleep)}, Readiness: {len(merged_readiness)})...")
    body = json.dumps(upload).encode("utf-8")
    req = urllib.request.Request(
        f"{RAILWAY_URL}/sync", data=body,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        log(f"✅ Server: HTTP {r.status}")

    return True, (
        f"Sünkroniseeritud! "
        f"UDS: {len(merged_uds)}p · Uni: {len(merged_sleep)}p · Readiness: {len(merged_readiness)}p"
    )


# ── HTTP server ────────────────────────────────────────────────────────────────

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
            self.wfile.write(json.dumps({
                "ok": None, "msg": last_result["msg"], "running": True
            }).encode())
            return

        logs = []

        def log(msg):
            print(msg)
            logs.append(msg)

        def set_msg(msg):
            global last_result
            last_result = {**last_result, "msg": msg}

        def run():
            global last_result
            last_result = {
                "ok": None,
                "msg": "🌐 Brauser avatud — logi sisse Garmin Connect'is",
                "running": True,
                "logs": [],
            }
            try:
                ok, msg = do_garmin_sync(log, set_msg)
            except Exception as e:
                ok, msg = False, f"Viga: {e}"
            last_result = {"ok": ok, "msg": msg, "running": False, "logs": logs}

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_cors()
        self.end_headers()
        self.wfile.write(json.dumps({
            "ok": None,
            "msg": "🌐 Brauser avatud — logi sisse Garmin Connect'is",
            "running": True,
        }).encode())


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("Garmin Sync Server (Playwright brauser-login)")
    print("=" * 55)

    # Kontrolli Playwright
    try:
        import playwright  # noqa: F401
        print("✅ Playwright installitud")
    except ImportError:
        print("❌ Playwright pole installitud!")
        print()
        print("Käivita:")
        print("  pip install playwright")
        print("  playwright install chromium")
        sys.exit(1)

    server = HTTPServer(("localhost", PORT), Handler)
    print(f"\n🚀 Server käib: http://localhost:{PORT}")
    print(f"   /sync   — avab brauseri Garmin sisselogimiseks")
    print(f"   /status — sync staatus")
    print()
    print("Vajuta rakenduses 'SYNC' nuppu!")
    print("Brauser avaneb automaatselt — logi Garmin Connect'i sisse.")
    print("Lõpetamiseks: Ctrl+C\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server peatatud")
