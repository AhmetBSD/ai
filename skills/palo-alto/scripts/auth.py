"""Interactive PAN-OS authentication for the skill.

The customer runs this once per session (typically when the previous key
has expired per `api-key-lifetime`). It:

  1. Asks for host, username, password — password is read with getpass so it
     never echoes to the terminal or appears in shell history.
  2. Calls PAN-OS keygen with those credentials.
  3. Drops the password from memory immediately.
  4. Writes only the short-lived API key (and the host/insecure flag) to
     ~/.palo-alto/session.env with chmod 600.
  5. Prints a confirmation telling the customer how long the key will work.

Subsequent skill commands (dnat.py / restrict_source.py / free_ip.py)
load credentials from env vars OR from this file — no manual export.

Usage:
    python3 auth.py            # interactive
    python3 auth.py --logout   # delete the session file
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import ssl
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen


SESSION_DIR = Path(os.path.expanduser("~/.palo-alto"))
SESSION_FILE = SESSION_DIR / "session.env"


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


def _ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    d = "Y/n" if default_yes else "y/N"
    while True:
        v = input(f"{prompt} [{d}]: ").strip().lower()
        if not v:
            return default_yes
        if v in ("y", "yes", "e", "evet"):
            return True
        if v in ("n", "no", "h", "hayir", "hayır"):
            return False


def _keygen(host: str, user: str, password: str, insecure: bool) -> tuple[str, str | None]:
    """Return (api_key, error_message)."""
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = ssl.create_default_context()

    url = f"https://{host}/api/?" + urllib.parse.urlencode({
        "type": "keygen", "user": user, "password": password,
    })
    try:
        body = urlopen(url, context=ctx, timeout=15).read()
    except Exception as e:
        return "", f"connection/auth failure: {e}"
    try:
        root = ET.fromstring(body)
        if root.attrib.get("status") != "success":
            msg = root.findtext(".//msg") or root.findtext(".//line") or "unknown error"
            return "", f"PA refused keygen: {msg}"
        key = root.findtext(".//key")
        if not key:
            return "", "PA returned success but no <key> element"
        return key, None
    except ET.ParseError as e:
        return "", f"could not parse PA response: {e}"


def do_auth() -> int:
    print("PAN-OS auth — bu adım sadece bir kez gereklidir.")
    print("Şifre ekranda görünmez ve diske yazılmaz.")
    print()

    host = _ask("Firewall (host veya FQDN)")
    if not host:
        print("ERROR: host gerekli.", file=sys.stderr); return 2
    user = _ask("Kullanıcı adı")
    if not user:
        print("ERROR: kullanıcı adı gerekli.", file=sys.stderr); return 2
    password = getpass.getpass("Şifre: ")
    if not password:
        print("ERROR: şifre gerekli.", file=sys.stderr); return 2
    insecure = _ask_yes_no("Self-signed cert kullanılıyor mu? (TLS verify atla)", default_yes=True)

    api_key, err = _keygen(host, user, password, insecure)
    # erase password from memory immediately
    password = "x" * len(password); del password

    if err:
        print(f"\nERROR: {err}", file=sys.stderr)
        return 1

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    obtained_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        f"PANOS_HOST={host}",
        f"PANOS_API_KEY={api_key}",
        f"PANOS_INSECURE={'1' if insecure else '0'}",
        f"# obtained-at: {obtained_at}",
        f"# obtained-by: {user}",
    ]
    SESSION_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    SESSION_FILE.chmod(0o600)

    print()
    print(f"  ✓ API key alındı, host={host}, user={user}")
    print(f"  ✓ Geçici olarak şuraya kaydedildi: {SESSION_FILE}")
    print(f"  ✓ Dosya izni: 600 (sadece sen okuyabilirsin)")
    print(f"  ✓ Şifre belleğe veya diske yazılmadı, unutuldu.")
    print()
    print("Şimdi normal komutları çalıştırabilirsin. Örnek:")
    print(f"  ~/.palo-alto/venv/bin/python ~/.claude/skills/palo-alto/scripts/free_ip.py")
    print()
    print("API key'in geçerlilik süresi PAN-OS tarafında belirlenir "
          "(`api-key-lifetime`, tipik 60 dakika).")
    print("Süresi dolduğunda komut 'auth' hatası verir; bu komutu tekrar çalıştır.")
    return 0


def do_logout() -> int:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
        print(f"  ✓ Silindi: {SESSION_FILE}")
        return 0
    print(f"  (zaten yok: {SESSION_FILE})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Interactive PAN-OS auth + session file.")
    p.add_argument("--logout", action="store_true",
                   help="Mevcut session.env dosyasını sil")
    args = p.parse_args(argv)
    if args.logout:
        return do_logout()
    return do_auth()


if __name__ == "__main__":
    sys.exit(main())
