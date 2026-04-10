#!/usr/bin/env python3
"""
Ühe käsuga seadistamine:
1. Logib Garminisse sisse ja salvestab tokenid
2. Seab GitHub Secrets: GARMIN_EMAIL, GARMIN_PASSWORD, RAILWAY_URL, GARMIN_TOKENS

Käivita: python setup_github_secrets.py
"""
import sys
import os
import json
import base64
import zipfile
import io
import getpass
import urllib.request
import urllib.error

REPO            = "hn-boop/kalori-tracker"
RAILWAY_URL     = "https://web-production-9ade8.up.railway.app"
TOKEN_DIR       = os.path.join(os.path.dirname(__file__), ".garmin_tokens")
CONFIG_FILE     = os.path.join(os.path.dirname(__file__), "garmin_config.json")

# ── GitHub Secrets API ─────────────────────────────────────────────────────────

def get_repo_public_key(github_token):
    """Saab repo avaliku võtme secretite krüptimiseks."""
    url = f"https://api.github.com/repos/{REPO}/actions/secrets/public-key"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def encrypt_secret(public_key_b64, secret_value):
    """Krüptib secreti NaCl sealed boxiga (GitHub nõue)."""
    from nacl import encoding, public
    pk = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder)
    sealed = public.SealedBox(pk)
    encrypted = sealed.encrypt(secret_value.encode())
    return base64.b64encode(encrypted).decode()

def set_github_secret(github_token, key_id, public_key_b64, name, value):
    """Seab ühe GitHub Secret."""
    encrypted = encrypt_secret(public_key_b64, value)
    url = f"https://api.github.com/repos/{REPO}/actions/secrets/{name}"
    body = json.dumps({"encrypted_value": encrypted, "key_id": key_id}).encode()
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (201, 204)
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()}")
        return False

# ── Garmin login + token export ────────────────────────────────────────────────

def garmin_login_and_export(email, password):
    """Logib Garminisse, salvestab tokenid, tagastab base64 zip."""
    try:
        import garminconnect
    except ImportError:
        print("❌ garminconnect pole installitud")
        sys.exit(1)

    print(f"🔑 Garmin login ({email})...")
    os.makedirs(TOKEN_DIR, exist_ok=True)
    try:
        garmin = garminconnect.Garmin(email, password, is_cn=False)
        garmin.login(tokenstore=TOKEN_DIR)
        name = garmin.get_full_name()
        print(f"✅ Sisselogimine õnnestus: {name}")
    except Exception as e:
        print(f"❌ Garmin login ebaõnnestus: {e}")
        return None

    # Ekspordi tokenid
    files = [f for f in os.listdir(TOKEN_DIR) if os.path.isfile(os.path.join(TOKEN_DIR, f))]
    if not files:
        print("❌ Tokeneid ei leitud pärast loginit")
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(os.path.join(TOKEN_DIR, f), f)
    b64 = base64.b64encode(buf.getvalue()).decode()
    print(f"✅ Tokenid eksporditud ({len(files)} faili, {len(b64)} chars)")
    return b64

# ── Peaskript ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  Garmin Sync — ühekordne seadistamine")
    print("=" * 55)
    print()

    # Lae olemasolev config kui on
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)

    # Garmin andmed
    print("── GARMIN CONNECT ─────────────────────────────────────")
    email = cfg.get("email") or input("Garmin email: ").strip()
    password = cfg.get("password") or getpass.getpass("Garmin parool: ")

    # GitHub PAT
    print()
    print("── GITHUB PERSONAL ACCESS TOKEN ───────────────────────")
    print("Vajad PAT-i GitHub Secrets seadistamiseks.")
    print("Loo: github.com → Settings → Developer settings → Fine-grained tokens")
    print("  Repo: hn-boop/kalori-tracker")
    print("  Permission: Actions = Read and write, Secrets = Read and write")
    print()
    github_token = getpass.getpass("GitHub PAT: ").strip()

    # Kontrolli GitHub tokenit
    print()
    print("── KONTROLLIN GITHUB ÜHENDUST ──────────────────────────")
    try:
        key_data = get_repo_public_key(github_token)
        key_id  = key_data["key_id"]
        pub_key = key_data["key"]
        print(f"✅ GitHub repo ligipääs OK")
    except Exception as e:
        print(f"❌ GitHub viga: {e}")
        print("  Kontrolli PAT-i — kas Actions + Secrets kirjutusõigus on sees?")
        sys.exit(1)

    # Garmin login
    print()
    print("── GARMIN LOGIN ────────────────────────────────────────")
    tokens_b64 = garmin_login_and_export(email, password)
    if not tokens_b64:
        sys.exit(1)

    # Salvesta lokaalne config
    cfg = {"email": email, "password": password}
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"✅ Lokaalne config salvestatud")

    # Sea GitHub Secrets
    print()
    print("── SEAB GITHUB SECRETS ─────────────────────────────────")
    secrets = {
        "GARMIN_EMAIL":    email,
        "GARMIN_PASSWORD": password,
        "RAILWAY_URL":     RAILWAY_URL,
        "GARMIN_TOKENS":   tokens_b64,
    }
    all_ok = True
    for name, value in secrets.items():
        ok = set_github_secret(github_token, key_id, pub_key, name, value)
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("=" * 55)
        print("  ✅ Kõik valmis!")
        print()
        print("  GitHub Actions käivitub automaatselt kell 08:00 EET.")
        print("  Sync nupp rakenduses töötab nüüd mobiilil.")
        print()
        print("  Testi kohe: mine GitHub Actions tab ja käivita")
        print("  'Garmin Sync' workflow käsitsi.")
        print("=" * 55)
    else:
        print("❌ Mõned secretid ei läinud — kontrolli PAT õiguseid")
        sys.exit(1)

if __name__ == "__main__":
    main()
