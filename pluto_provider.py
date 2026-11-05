
import gzip
import json
import os
import uuid

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


OUTPUT = "pluto_es.m3u"

EPG = (
    "https://github.com/matthuisman/i.mjh.nz/raw/"
    "refs/heads/master/PlutoTV/es.xml.gz"
)

CATALOG = (
    "https://github.com/matthuisman/i.mjh.nz/raw/"
    "refs/heads/master/PlutoTV/.channels.json.gz"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
    ),
    "Origin": "https://pluto.tv",
    "Referer": "https://pluto.tv/",
    "X-Forwarded-For": "88.26.241.248",
}

# Sesión HTTP con reintentos
session = requests.Session()
session.headers.update(HEADERS)

retry = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)

session.mount(
    "https://",
    HTTPAdapter(max_retries=retry)
)


def get_json(url, **kwargs):
    response = session.get(url, timeout=25, **kwargs)
    response.raise_for_status()
    return response.json()


def get_catalog():
    print("Descargando catálogo de reproducción...")

    response = session.get(CATALOG, timeout=30)
    response.raise_for_status()

    data = gzip.decompress(response.content)
    catalog = json.loads(data)

    # Exclusivamente canales de España
    channels = (
        catalog
        .get("regions", {})
        .get("es", {})
        .get("channels", {})
    )

    if not channels:
        raise RuntimeError(
            "No se encuentra el catálogo español"
        )

    return channels


def get_pluto_data():
    print("Obteniendo sesión de Pluto...")

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
        "drmCapabilities": "widevine:L3",
    }

    start = get_json(
        "https://boot.pluto.tv/v4/start",
        params=params
    )

    token = start.get("sessionToken")

    if not token:
        raise RuntimeError(
            "No se recibió el token de Pluto"
        )

    auth_headers = {
        "Authorization": f"Bearer {token}"
    }

    print("Descargando canales y categorías españolas...")

    channels = get_json(
        "https://service-channels.clusters.pluto.tv/v2/guide/channels",
        params={"limit": 1000},
        headers=auth_headers,
    ).get("data", [])

    categories = get_json(
        "https://service-channels.clusters.pluto.tv/v2/guide/categories",
        headers=auth_headers,
    ).get("data", [])

    channel_map = {
        channel["id"]: channel
        for channel in channels
        if channel.get("id")
    }

    category_map = {}

    for category in categories:
        category_name = category.get("name", "Pluto TV")

        for channel_id in category.get("channelIDs", []):
            category_map[channel_id] = category_name

    return channel_map, category_map


def generate(catalog, live_channels, categories):
    lines = [f'#EXTM3U url-tvg="{EPG}"\n']

    count = 0

    # Orden alfabético, como el generador original
    ordered_channels = sorted(
        catalog.items(),
        key=lambda item: str(
            live_channels.get(item[0], {}).get("name")
            or item[1].get("name")
            or ""
        ).lower()
    )

    for channel_id, channel in ordered_channels:

        live = live_channels.get(channel_id, {})

        name = (
            live.get("name")
            or channel.get("name")
        )

        if not name:
            continue

        # Logotipo del canal
        logo = next(
            (
                image.get("url")
                for image in live.get("images", [])
                if image.get("type") == "colorLogoPNG"
            ),
            None,
        ) or channel.get("logo", "")

        # Categoría
        group = categories.get(
            channel_id,
            channel.get("group", "Pluto TV")
        )

        chno = channel.get("chno", "")

        # Formato de atributos del generador original.
        # No aplicar escape HTML a los metadatos.
        attrs = {
            "channel-id": channel_id,
            "tvg-id": channel_id,
            "tvg-chno": chno,
            "tvg-name": name,
            "tvg-logo": logo,
            "group-title": group,
        }

        metadata = " ".join(
            f'{key}="{str(value).replace(chr(34), chr(39))}"'
            for key, value in attrs.items()
        )

        display_name = str(name).replace(",", "")

        lines.append(
            f"#EXTINF:-1 {metadata},{display_name}\n"
        )

        # Mantener la URL original de reproducción
        lines.append(
            f"https://jmp2.uk/plu-{channel_id}.m3u8\n"
        )

        count += 1

    if count == 0:
        raise RuntimeError(
            "No se han generado canales"
        )

    return "".join(lines), count


def main():
    catalog = get_catalog()

    live_channels, categories = get_pluto_data()

    content, count = generate(
        catalog,
        live_channels,
        categories
    )

    # Escritura segura del archivo
    temp_file = OUTPUT + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
        newline="\n"
    ) as file:
        file.write(content)

    os.replace(temp_file, OUTPUT)

    print("--------------------------------")
    print(f"España: {count} canales generados")
    print(
        f"Categorías: {len(set(categories.values()))}"
    )
    print(f"Archivo: {OUTPUT}")
    print("--------------------------------")


if __name__ == "__main__":
    main()
