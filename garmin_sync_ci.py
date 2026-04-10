#!/usr/bin/env python3
"""
Garmin sync skript GitHub Actions jaoks.
Loeb GARMIN_EMAIL, GARMIN_PASSWORD, RAILWAY_URL keskkonnast.
"""
import os
import sys
import json
import urllib.request
from datetime import date, timedelta, datetime

GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")
RAILWAY_URL = os.environ.get("RAILWAY_URL", "https://web-production-9ade8.up.railway.app")
DAYS_TO_FETCH = 30  # CI-s piisab viimasest 30 päevast

def http_get(url, timeout=20):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
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

def main():
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print("❌ GARMIN_EMAIL või GARMIN_PASSWORD pole seadistatud GitHub Secrets-is")
        sys.exit(1)

    try:
        import garminconnect
    except ImportError:
        print("❌ garminconnect pole installitud")
        sys.exit(1)

    # Autentimine (CI-s ei saa tokeneid salvestada, aga kord päevas on ok)
    print(f"🔑 Garmin autentimine ({GARMIN_EMAIL})...")
    try:
        garmin = garminconnect.Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        garmin.login()
        print("✅ Sisselogimine õnnestus")
    except Exception as e:
        print(f"❌ Login ebaõnnestus: {e}")
        sys.exit(1)

    today = date.today()
    start = today - timedelta(days=DAYS_TO_FETCH)

    uds_data = []
    sleep_data = []
    readiness_data = []

    print(f"📥 Laen andmeid: {start} → {today}...")

    current = start
    while current <= today:
        ds = current.strftime("%Y-%m-%d")

        # UDS
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
            print(f"  ⚠️  UDS {ds}: {e}")

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
            print(f"  ⚠️  Uni {ds}: {e}")

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
            print(f"  ⚠️  Readiness {ds}: {e}")

        current += timedelta(days=1)

    print(f"📊 UDS: {len(uds_data)}p · Uni: {len(sleep_data)}p · Readiness: {len(readiness_data)}p")

    # Laeme Railway serverist olemasolevad andmed
    print("☁️  Laen serverist olemasolevad andmed...")
    server_data = {}
    try:
        server_data = http_get(f"{RAILWAY_URL}/sync")
    except Exception as e:
        print(f"  ⚠️  Serveri lugemine: {e}")

    # Merge
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

    print(f"☁️  Laen üles (UDS: {len(merged_uds)}, Uni: {len(merged_sleep)}, Readiness: {len(merged_readiness)})...")
    try:
        status = http_post(f"{RAILWAY_URL}/sync", upload)
        print(f"✅ Valmis! HTTP {status}")
    except Exception as e:
        print(f"❌ Upload ebaõnnestus: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
