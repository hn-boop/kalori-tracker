#!/usr/bin/env python3
"""
Garmin sync skript GitHub Actions jaoks.
Loeb GARMIN_EMAIL, GARMIN_PASSWORD, RAILWAY_URL, GARMIN_TOKENS keskkonnast.
"""
import os
import sys
import json
import zipfile
import base64
import io
import urllib.request
from datetime import date, timedelta, datetime

GARMIN_EMAIL   = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")
RAILWAY_URL    = os.environ.get("RAILWAY_URL", "https://web-production-9ade8.up.railway.app")
GARMIN_TOKENS  = os.environ.get("GARMIN_TOKENS", "")   # base64 zip tokenite kaustast
TOKEN_DIR      = ".garmin_tokens"
DAYS_TO_FETCH  = 30

# ── Token import/export ────────────────────────────────────────────────────────

def import_tokens(b64_str, token_dir):
    """Laeb base64-kodeeritud tokenid kausta."""
    try:
        data = base64.b64decode(b64_str)
        os.makedirs(token_dir, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(token_dir)
        print(f"✅ Tokenid laaditud: {os.listdir(token_dir)}")
        return True
    except Exception as e:
        print(f"⚠️  Tokenite laadimine ebaõnnestus: {e}")
        return False

def export_tokens(token_dir):
    """Tagastab base64-kodeeritud tokenid."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in os.listdir(token_dir):
            zf.write(os.path.join(token_dir, f), f)
    return base64.b64encode(buf.getvalue()).decode()

# ── HTTP ───────────────────────────────────────────────────────────────────────

def http_get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
        return json.loads(r.read())

def http_post(url, data, timeout=30):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print("❌ GARMIN_EMAIL või GARMIN_PASSWORD pole GitHub Secrets-is seadistatud")
        sys.exit(1)

    try:
        import garminconnect
        print(f"✅ garminconnect {garminconnect.__version__}")
    except (ImportError, AttributeError):
        try:
            import garminconnect
            print("✅ garminconnect installitud")
        except ImportError:
            print("❌ garminconnect pole installitud")
            sys.exit(1)

    # Tokenite taastamine (kui GARMIN_TOKENS pole juba töödeldud workflow poolt)
    if GARMIN_TOKENS and not os.path.exists(TOKEN_DIR):
        import_tokens(GARMIN_TOKENS, TOKEN_DIR)

    # Garmin autentimine
    print(f"🔑 Garmin autentimine ({GARMIN_EMAIL})...")
    try:
        garmin = garminconnect.Garmin(
            GARMIN_EMAIL, GARMIN_PASSWORD,
            is_cn=False,
            tokenstore=TOKEN_DIR
        )
        garmin.login()
        print("✅ Sisselogimine õnnestus")
    except Exception as e:
        print(f"❌ Garmin login ebaõnnestus: {type(e).__name__}: {e}")
        print("\nVõimalikud põhjused:")
        print("  1. Vale GARMIN_EMAIL / GARMIN_PASSWORD")
        print("  2. Garmin blokeerib GitHub Actions IP-d → lisa GARMIN_TOKENS secret")
        print("  3. Kahe-faktoriga autentimine on sees")
        print("\nLahendus: käivita kohalikult 'python sync_server.py --export-tokens'")
        print("ja lisa tulemus GitHub Secrets-isse nime GARMIN_TOKENS alla")
        sys.exit(1)

    today = date.today()
    start = today - timedelta(days=DAYS_TO_FETCH)

    uds_data = []
    sleep_data = []
    readiness_data = []

    print(f"📥 Laen andmeid: {start} → {today}...")

    current = start
    errors = 0
    while current <= today:
        ds = current.strftime("%Y-%m-%d")

        # UDS (sammud, kalorid, HR, stress)
        try:
            stats = garmin.get_stats(ds)
            if stats and stats.get("totalKilocalories"):
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
            errors += 1
            if errors <= 3:
                print(f"  ⚠️  UDS {ds}: {type(e).__name__}: {e}")

        # Uni
        try:
            sleep = garmin.get_sleep_data(ds)
            if sleep:
                dto = sleep.get("dailySleepDTO") or {}
                scores = sleep.get("sleepScores") or {}
                if dto.get("calendarDate") or dto.get("sleepTimeSeconds"):
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
            errors += 1
            if errors <= 3:
                print(f"  ⚠️  Uni {ds}: {type(e).__name__}: {e}")

        # Training Readiness
        try:
            rdns = garmin.get_training_readiness(ds)
            if rdns:
                items = rdns if isinstance(rdns, list) else [rdns]
                for r in items:
                    if isinstance(r, dict) and r.get("score"):
                        readiness_data.append({
                            "calendarDate": r.get("calendarDate", ds),
                            "score": r.get("score"),
                            "level": r.get("level"),
                            "hrvFactorPercent": r.get("hrvFactorPercent"),
                            "hrvWeeklyAverage": r.get("hrvWeeklyAverage"),
                        })
                        break
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ⚠️  Readiness {ds}: {type(e).__name__}: {e}")

        current += timedelta(days=1)

    print(f"📊 UDS: {len(uds_data)}p · Uni: {len(sleep_data)}p · Readiness: {len(readiness_data)}p ({errors} viga)")

    if not uds_data and not sleep_data:
        print("❌ Andmeid ei saadud — kontrolli logimisviga ülal")
        sys.exit(1)

    # Laeme Railway serverist olemasolevad andmed
    print("☁️  Laen serverist olemasolevad andmed...")
    server_data = {}
    try:
        server_data = http_get(f"{RAILWAY_URL}/sync")
        print(f"   Server: {len(json.dumps(server_data)) // 1024} KB")
    except Exception as e:
        print(f"  ⚠️  Serveri lugemine: {e}")

    # Merge: olemasolev + uued (uued kirjutavad üle)
    def merge_by_date(existing_list, new_list):
        m = {}
        for r in (existing_list or []):
            if r.get("calendarDate"):
                m[r["calendarDate"]] = r
        for r in new_list:
            if r.get("calendarDate"):
                m[r["calendarDate"]] = r
        return sorted(m.values(), key=lambda x: x["calendarDate"])

    existing = server_data.get("garmin_data") or {}
    merged_uds       = merge_by_date(existing.get("uds"),       uds_data)
    merged_sleep     = merge_by_date(existing.get("sleep"),     sleep_data)
    merged_readiness = merge_by_date(existing.get("readiness"), readiness_data)

    upload = dict(server_data)
    upload["garmin_data"] = {
        "exportedAt": datetime.now().isoformat(),
        "uds":        merged_uds,
        "sleep":      merged_sleep,
        "readiness":  merged_readiness,
    }

    total_days = len(set(r["calendarDate"] for r in merged_uds))
    print(f"☁️  Laen üles: UDS {len(merged_uds)}p (kokku {total_days} unikaalset) · Uni {len(merged_sleep)}p · Readiness {len(merged_readiness)}p")

    try:
        status = http_post(f"{RAILWAY_URL}/sync", upload)
        print(f"✅ Valmis! HTTP {status}")
    except Exception as e:
        print(f"❌ Upload ebaõnnestus: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
