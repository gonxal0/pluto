
import gzip
import html
import uuid
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OUTPUT = "pluto_es.m3u"
EPG = "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/es.xml.gz"

CATALOG = "https://github.com/matthuisman/i.mjh.nz/raw/refs/heads/master/PlutoTV/.channels.json.gz"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36",
    "Origin": "https://pluto.tv",
    "Referer": "https://pluto.tv/",
    "X-Forwarded-For": "88.26.241.248"
}

s = requests.Session()
s.headers.update(HEADERS)

retry = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)
s.mount("https://", HTTPAdapter(max_retries=retry))


def get_json(url, **kwargs):
    r = s.get(url, timeout=25, **kwargs)
    r.raise_for_status()
    return r.json()


def get_catalog():
    print("Descargando catálogo de reproducción...")
    r = s.get(CATALOG, timeout=30)
    r.raise_for_status()

    data = gzip.decompress(r.content)
    catalog = __import__("json").loads(data)

    channels = catalog.get("regions", {}).get("es", {}).get("channels", {})

    if not channels:
        raise RuntimeError("No se encuentra el catálogo español")

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
        "drmCapabilities": "widevine:L3"
    }

    start = get_json("https://boot.pluto.tv/v4/start", params=params)
    token = start.get("sessionToken")

    if not token:
        raise RuntimeError("No se recibió el token de Pluto")

    headers = {"Authorization": f"Bearer {token}"}

    print("Descargando canales y categorías españolas...")

    channels = get_json(
        "https://service-channels.clusters.pluto.tv/v2/guide/channels",
        params={"limit": 1000},
        headers=headers
    ).get("data", [])

    categories = get_json(
        "https://service-channels.clusters.pluto.tv/v2/guide/categories",
        headers=headers
    ).get("data", [])

    channel_map = {
        ch["id"]: ch for ch in channels if ch.get("id")
    }

    category_map = {}

    for category in categories:
        for channel_id in category.get("channelIDs", []):
            category_map[channel_id] = category.get("name", "Pluto TV")

    return channel_map, category_map


def generate(catalog, live_channels, categories):
    lines = [f'#EXTM3U url-tvg="{EPG}"\n']
    count = 0

    for channel_id, ch in catalog.items():

        live = live_channels.get(channel_id, {})

        name = live.get("name") or ch.get("name")
        if not name:
            continue

        logo = next(
            (
                img.get("url")
                for img in live.get("images", [])
                if img.get("type") == "colorLogoPNG"
            ),
            None
        ) or ch.get("logo", "")

        group = categories.get(
            channel_id,
            ch.get("group", "Pluto TV")
        )

        chno = ch.get("chno", "")

        # Formato compatible con el generador original.
        attrs = {
            "channel-id": channel_id,
            "tvg-id": channel_id,
            "tvg-chno": chno,
            "tvg-name": name,
            "tvg-logo": logo,
            "group-title": group
        }

        metadata = " ".join(
            f'{key}="{html.escape(str(value), quote=True)}"'
            for key, value in attrs.items()
        )

        display_name = str(name).replace(",", "")

        lines.append(f"#EXTINF:-1 {metadata},{display_name}\n")
        lines.append(f"https://jmp2.uk/plu-{channel_id}.m3u8\n")

        count += 1

    if not count:
        raise RuntimeError("No se han generado canales")

    return "".join(lines), count


def main():
    catalog = get_catalog()
    live_channels, categories = get_pluto_data()

    content, count = generate(catalog, live_channels, categories)

    with open(OUTPUT + ".tmp", "w", encoding="utf-8") as f:
        f.write(content)

    import os
    os.replace(OUTPUT + ".tmp", OUTPUT)

    print(f"España: {count} canales generados")
    print(f"Categorías: {len(set(categories.values()))}")
    print(f"Archivo: {OUTPUT}")


if __name__ == "__main__":
    main()
