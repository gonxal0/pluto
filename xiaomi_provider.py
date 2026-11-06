#!/usr/bin/env python3
"""Build a Spain-filtered Xiaomi TV+ M3U from Xiaomi's own catalog API."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


API_BASE = "https://mitvplus.global.mi.com"
API_PATH = "/mitv/guide"
APP_ID = "4"
REGION = "ES"
LANGUAGE = "es"
# These values are embedded in the Xiaomi TV+ APK's PWAppInterceptor.
SIGNING_TOKEN = "0f9dfa001cba164d7bda671649c50abf"
SIGNING_KEY = "581582928c881b42eedce96331bff5d3"
APP_BUNDLE = "com.mitv.tvhome.mitvplus"
APP_NAME = "Xiaomi TV+"
APP_STORE_URL = "https://play.google.com/store/apps/details?id=com.mitv.tvhome.mitvplus"
OUTPUT = Path(__file__).with_name("xiaomi.m3u")
USER_AGENT = "okhttp/4.9.3"

GROUP_NAMES = {
    "For You": "Para ti",
    "New on XTV+": "Nuevo en Xiaomi TV+",
    "Music": "Música",
    "Movie": "Cine",
    "Lifestyle": "Estilo de vida",
    "Sports & Outdoor": "Deportes y aire libre",
    "Entertainment": "Entretenimiento",
}


def _catalog_url() -> str:
    params = [("app", APP_ID), ("region", REGION), ("dlg", LANGUAGE)]
    query = urllib.parse.urlencode(params)
    signed_text = f"{API_PATH}?{query}&token={SIGNING_TOKEN}"
    opaque = hmac.new(
        SIGNING_KEY.encode("utf-8"), signed_text.encode("utf-8"), hashlib.sha1
    ).hexdigest()
    return f"{API_BASE}{API_PATH}?{query}&opaque={opaque}"


def fetch_catalog() -> dict:
    request = urllib.request.Request(
        _catalog_url(),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except Exception as exc:
        raise RuntimeError(f"Could not fetch Xiaomi TV+ catalog: {exc}") from exc

    try:
        catalog = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Xiaomi TV+ returned invalid JSON.") from exc

    if catalog.get("status") != 0:
        raise RuntimeError(f"Xiaomi TV+ API returned status {catalog.get('status')}.")
    if not isinstance(catalog.get("data", {}).get("blocks"), list):
        raise RuntimeError("Xiaomi TV+ response has no guide sections.")
    return catalog


def _play_url(channel: dict) -> str:
    """Resolve playback macros in Xiaomi's own playurl template."""
    url = channel.get("playurl") or channel.get("intent") or ""
    if not url.startswith(("https://", "http://")):
        return ""

    values = {
        "app_bundle": APP_BUNDLE,
        "app_name": APP_NAME,
        "app_store_url": APP_STORE_URL,
        "ifa_type": "aaid",
        "device_id": "00000000-0000-4000-8000-000000000000",
        "gdpr": "0",
        "consent": "",
        "is_lat": "0",
        "us_privacy": "1---",
        "platform": "androidtv",
        "device_type": "tv",
        "device_height": "720",
        "device_width": "1280",
        "device_language": LANGUAGE,
        "device_make": "Xiaomi",
        "device_model": "TV",
        "custom": "",
        "nonce": "",
    }
    for key, value in values.items():
        encoded = urllib.parse.quote(value, safe="")
        url = url.replace("{" + key + "}", encoded)
        url = url.replace("%7B" + key.upper() + "%7D", encoded)
        url = url.replace("[" + key.upper() + "]", encoded)
        url = url.replace("%5B" + key.upper() + "%5D", encoded)

    # The APK fills these channel/ad metadata fields from its own ad config.
    url = url.replace("REPLACE_WITH_URL_ENCODED_URL", urllib.parse.quote(APP_STORE_URL, safe=""))
    url = re.sub(r"REPLACE_[A-Z_]+", "", url)
    url = re.sub(r"\{[^{}]*\}", "", url)
    url = urllib.parse.quote(url, safe=":/?&=%#[]@!$'()*+,;~-._")
    return url


def _attr(value: object) -> str:
    return str(value or "").replace('"', "'").replace("\r", " ").replace("\n", " ")


def render_playlist(catalog: dict) -> tuple[str, int]:
    lines = ["#EXTM3U"]
    count = 0
    for block in catalog["data"]["blocks"]:
        group = GROUP_NAMES.get(block.get("title", ""), block.get("title", "Xiaomi TV+"))
        for channel in block.get("channels", []):
            regions = {str(region).upper() for region in channel.get("regions", [])}
            if not regions.intersection({"ES", "GLOBAL", "ALL"}):
                continue

            name = str(channel.get("title") or "").strip()
            url = _play_url(channel)
            if not name or not url:
                continue

            channel_id = channel.get("id", "")
            icon = (channel.get("images") or {}).get("icon", {}).get("link", "")
            attributes = [f'tvg-id="{_attr(channel_id)}"']
            if icon:
                attributes.append(f'tvg-logo="{_attr(icon)}"')
            attributes.append(f'group-title="{_attr(group)}"')
            lines.append(f"#EXTINF:-1 {' '.join(attributes)},{name}")
            lines.append(url)
            count += 1

    if count == 0:
        raise RuntimeError("No channels available in Xiaomi TV+ for Spain.")
    return "\n".join(lines) + "\n", count


def write_atomically(content: str) -> None:
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{OUTPUT.name}.", suffix=".tmp", dir=OUTPUT.parent, text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as target:
            target.write(content)
        os.replace(temp_name, OUTPUT)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> None:
    playlist, count = render_playlist(fetch_catalog())
    write_atomically(playlist)
    print(f"Updated {OUTPUT.name}: {count} Spain-available channel entries from Xiaomi TV+ API")


if __name__ == "__main__":
    main()

