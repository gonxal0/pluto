
import os
import uuid
import requests
from html import escape

REGION = "es"
OUTPUT = "pluto_es.m3u"
EPG = "https://github.com/matthuisman/i.mjh.nz/raw/master/PlutoTV/es.xml.gz"

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Origin": "https://pluto.tv",
    "Referer": "https://pluto.tv/",
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9"
}

# Se conserva la configuración regional de tu código original.
BASE_HEADERS["X-Forwarded-For"] = "88.26.241.248"

session = requests.Session()
session.headers.update(BASE_HEADERS)


def get_token():
    params = {
        "appName": "web",
        "appVersion": "8.1.0",
        "deviceVersion": "133.0.0",
        "deviceModel": "web",
        "deviceMake": "chrome",
        "deviceType": "web",
        "clientID": str(uuid.uuid4()),
        "clientModelNumber": "1.0.0",
        "serverSideAds": "false",
        "architecture": "x86_64",
        "buildVersion": "1.0.0",
        "drmCapabilities": "widevine:L3"
    }

    response = session.get(
        "https://boot.pluto.tv/v4/start",
        params=params,
        timeout=30
    )
    response.raise_for_status()

    token = response.json().get("sessionToken")
    if not token:
        raise RuntimeError("Pluto no ha devuelto un token")

    return token


def get_channels(token):
    headers = {"Authorization": f"Bearer {token}"}

    response = session.get(
        "https://service-channels.clusters.pluto.tv/v2/guide/channels",
        params={"limit": 1000},
        headers=headers,
        timeout=30
    )
    response.raise_for_status()

    channels = response.json().get("data", [])
    if not channels:
        raise RuntimeError("No se han recibido canales de Pluto")

    return channels, headers


def get_categories(headers):
    response = session.get(
        "https://service-channels.clusters.pluto.tv/v2/guide/categories",
        headers=headers,
        timeout=30
    )
    response.raise_for_status()

    categories = {}
    for category in response.json().get("data", []):
        name = category.get("name", "Pluto TV")
        for channel_id in category.get("channelIDs", []):
            categories[channel_id] = name

    return categories


def generate_m3u(channels, categories):
    lines = [f'#EXTM3U url-tvg="{EPG}"\n']
    seen = set()

    for channel in channels:
        channel_id = channel.get("id")
        name = channel.get("name")

        if not channel_id or not name or channel_id in seen:
            continue

        seen.add(channel_id)

        logo = next(
            (
                img.get("url", "")
                for img in channel.get("images", [])
                if img.get("type") == "colorLogoPNG"
            ),
            ""
        )

        group = categories.get(channel_id, "Pluto TV")

        name_attr = escape(str(name), quote=True)
        logo_attr = escape(str(logo), quote=True)
        group_attr = escape(str(group), quote=True)

        lines.append(
            f'#EXTINF:-1 channel-id="{channel_id}" '
            f'tvg-id="{channel_id}" '
            f'tvg-name="{name_attr}" '
            f'tvg-logo="{logo_attr}" '
            f'group-title="{group_attr}",{name}\n'
        )

        # Mismo sistema de reproducción que el repositorio compatible.
        lines.append(f"https://jmp2.uk/plu-{channel_id}.m3u8\n")

    if not seen:
        raise RuntimeError("No se han generado canales")

    return "".join(lines), len(seen)


def main():
    print("Obteniendo sesión de Pluto España...")
    token = get_token()

    print("Descargando canales...")
    channels, headers = get_channels(token)

    print("Obteniendo categorías...")
    categories = get_categories(headers)

    print("Generando lista M3U...")
    content, total = generate_m3u(channels, categories)

    # No sobrescribir una lista válida con una salida vacía.
    temp_file = OUTPUT + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        file.write(content)

    os.replace(temp_file, OUTPUT)

    print(f"Lista generada: {OUTPUT}")
    print(f"Canales: {total}")
    print(f"Categorías: {len(set(categories.values()))}")


if __name__ == "__main__":
    main()
