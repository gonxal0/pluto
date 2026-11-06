
import gzip
import json
import os
import uuid
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

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

BOOT_URL = "https://boot.pluto.tv/v4/start"

CHANNELS_URL = (
    "https://service-channels.clusters.pluto.tv/"
    "v2/guide/channels"
)

CATEGORIES_URL = (
    "https://service-channels.clusters.pluto.tv/"
    "v2/guide/categories"
)

STITCHER_FALLBACK = (
    "https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv"
)

# IP regional utilizada por la implementación OpenViX para España.
# Se puede cambiar mediante la variable de entorno PLUTO_X_FORWARDED_FOR.
SPAIN_X_FORWARDED_FOR = os.getenv(
    "PLUTO_X_FORWARDED_FOR",
    "88.26.241.248"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    ),
    "Origin": "https://pluto.tv",
    "Referer": "https://pluto.tv/",
    "X-Forwarded-For": SPAIN_X_FORWARDED_FOR,
}

# Sesión HTTP reutilizable con reintentos.
session = requests.Session()
session.headers.update(HEADERS)

retry = Retry(
    total=3,
    connect=3,
    read=2,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    respect_retry_after_header=True,
)

adapter = HTTPAdapter(
    max_retries=retry,
    pool_connections=10,
    pool_maxsize=10,
)

session.mount("https://", adapter)


def get_json(url, **kwargs):
    """Realiza una petición HTTP y devuelve JSON."""
    response = session.get(
        url,
        timeout=(10, 30),
        **kwargs
    )
    response.raise_for_status()
    return response.json()


def get_catalog():
    """Descarga exclusivamente el catálogo regional español."""
    print("Descargando catálogo de Pluto España...")

    response = session.get(CATALOG, timeout=(10, 30))
    response.raise_for_status()

    try:
        data = gzip.decompress(response.content)
    except gzip.BadGzipFile:
        # Por si el servidor ya entrega el contenido descomprimido.
        data = response.content

    catalog = json.loads(data)

    channels = (
        catalog
        .get("regions", {})
        .get("es", {})
        .get("channels", {})
    )

    if not isinstance(channels, dict) or not channels:
        raise RuntimeError(
            "No se encuentra el catálogo español."
        )

    print(f"Catálogo español: {len(channels)} entradas")
    return channels


def get_pluto_session():
    """
    Obtiene un JWT nuevo y los datos de reproducción proporcionados
    por Pluto: servidor stitcher y stitcherParams.
    """
    print("Solicitando sesión nueva a Pluto...")

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
        BOOT_URL,
        params=params
    )

    token = start.get("sessionToken")

    if not token:
        raise RuntimeError(
            "Pluto no ha devuelto sessionToken."
        )

    servers = start.get("servers") or {}

    stitcher = (
        servers.get("stitcher")
        or STITCHER_FALLBACK
    ).rstrip("/")

    stitcher_params = start.get("stitcherParams") or ""

    print("Sesión de Pluto obtenida.")
    print(f"Servidor stitcher: {stitcher}")

    return {
        "token": token,
        "stitcher": stitcher,
        "stitcher_params": stitcher_params,
    }


def get_pluto_data(token):
    """Obtiene canales y categorías de la API de Pluto."""
    print("Descargando información de canales y categorías...")

    auth_headers = {
        "Authorization": f"Bearer {token}"
    }

    channels_response = get_json(
        CHANNELS_URL,
        params={
            "channelIds": "",
            "offset": 0,
            "limit": 1000,
            "sort": "number:asc",
        },
        headers=auth_headers,
    )

    categories_response = get_json(
        CATEGORIES_URL,
        headers=auth_headers,
    )

    channels = channels_response.get("data", [])
    categories = categories_response.get("data", [])

    # Indexamos los canales por ambos identificadores cuando existan.
    channel_map = {}

    for channel in channels:
        channel_id = channel.get("id")
        original_id = channel.get("original_id")

        if channel_id:
            channel_map[channel_id] = channel

        if original_id:
            # El ID canónico (channel.id) siempre tiene prioridad sobre
            # los alias original_id para evitar que un alias sobrescriba
            # otro canal con el mismo identificador.
            channel_map.setdefault(original_id, channel)

    category_map = {}

    for category in categories:
        category_name = category.get("name", "Pluto TV")

        for channel_id in category.get("channelIDs", []):
            category_map[channel_id] = category_name

    print(f"Canales recibidos de Pluto: {len(channels)}")
    print(f"Categorías recibidas: {len(categories)}")

    return channel_map, category_map


def build_stream_url(channel_id, pluto_session):
    """
    Construye la URL HLS directa de Pluto.

    Utiliza:
      - El servidor stitcher devuelto por boot.
      - El JWT de la sesión.
      - Los stitcherParams devueltos por Pluto.
    """
    token = pluto_session["token"]
    stitcher = pluto_session["stitcher"]
    stitcher_params = pluto_session["stitcher_params"]

    url = (
        f"{stitcher}/v2/stitch/hls/channel/"
        f"{channel_id}/master.m3u8"
    )

    # Parámetros adicionales devueltos por Pluto.
    if isinstance(stitcher_params, dict):
        extra_params = list(stitcher_params.items())

    elif isinstance(stitcher_params, str):
        extra_params = parse_qsl(
            stitcher_params.lstrip("?&"),
            keep_blank_values=True,
        )

    else:
        extra_params = []

    # Evitamos duplicar los parámetros principales.
    extra_params = [
        (key, value)
        for key, value in extra_params
        if key not in ("jwt", "masterJWTPassthrough")
    ]

    query_params = [
        ("jwt", token),
        ("masterJWTPassthrough", "true"),
    ]

    query_params.extend(extra_params)

    return f"{url}?{urlencode(query_params)}"


def get_logo(live, catalog_channel):
    """Busca el logotipo disponible del canal."""
    for image in live.get("images", []):
        if image.get("type") == "colorLogoPNG" and image.get("url"):
            return image["url"]

    return catalog_channel.get("logo", "")


def generate(catalog, live_channels, categories, pluto_session):
    """Genera la playlist M3U de Pluto TV España."""
    lines = [
        f'#EXTM3U url-tvg="{EPG}"\n'
    ]

    count = 0
    used_ids = set()

    ordered_channels = sorted(
        catalog.items(),
        key=lambda item: str(
            live_channels.get(
                item[1].get("original_id", item[0]), {}
            ).get("name")
            or item[1].get("name")
            or ""
        ).lower()
    )

    for catalog_id, channel in ordered_channels:

        # Resolvemos primero el registro vivo usando el ID del catálogo.
        # Si Pluto devuelve un ID canónico distinto, ese es el que debe
        # utilizarse en el endpoint /channel/{id}/master.m3u8.
        catalog_original_id = channel.get("original_id")
        live = (
            live_channels.get(catalog_id)
            or live_channels.get(catalog_original_id)
            or {}
        )

        stream_id = (
            live.get("id")
            or catalog_original_id
            or catalog_id
        )

        if not stream_id or stream_id in used_ids:
            continue

        if str(channel.get("group", "")).strip().lower() in (
            "música", "musica", "music"
        ):
            print(
                "[MUSIC ID] "
                f"name={live.get('name') or channel.get('name')} "
                f"catalog_id={catalog_id} "
                f"catalog_original_id={catalog_original_id} "
                f"live_id={live.get('id')} "
                f"live_original_id={live.get('original_id')} "
                f"stream_id={stream_id} "
                f"available={live.get('isAvailable', live.get('available'))}"
            )

        name = (
            live.get("name")
            or channel.get("name")
        )

        if not name:
            continue

        logo = get_logo(live, channel)

        group = (
            categories.get(stream_id)
            or categories.get(catalog_id)
            or channel.get("group")
            or "Pluto TV"
        )

        chno = channel.get("chno", "")

        attrs = {
            "channel-id": catalog_id,
            "tvg-id": catalog_id,
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

        # URL directa de Pluto, sin jmp2.uk.
        lines.append(
            build_stream_url(stream_id, pluto_session) + "\n"
        )

        used_ids.add(stream_id)
        count += 1

    if count == 0:
        raise RuntimeError(
            "No se han generado canales."
        )

    return "".join(lines), count


def validate_playlist(content, count):
    """Comprueba que la playlist contiene URLs directas válidas."""
    if not content.startswith("#EXTM3U"):
        raise RuntimeError("Cabecera M3U incorrecta.")

    if count == 0:
        raise RuntimeError("Playlist vacía.")

    if "master.m3u8?jwt=" not in content:
        raise RuntimeError(
            "No se han encontrado URLs directas con JWT."
        )

    if "jmp2.uk" in content:
        raise RuntimeError(
            "La playlist todavía contiene URLs jmp2.uk."
        )

    if content.count("#EXTINF:") != count:
        raise RuntimeError(
            "El número de entradas EXTINF no coincide."
        )


def main():
    catalog = get_catalog()

    pluto_session = get_pluto_session()

    live_channels, categories = get_pluto_data(
        pluto_session["token"]
    )

    content, count = generate(
        catalog,
        live_channels,
        categories,
        pluto_session,
    )

    validate_playlist(content, count)

    temp_file = OUTPUT + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as file:
        file.write(content)

    os.replace(temp_file, OUTPUT)

    print("--------------------------------")
    print(f"País: España")
    print(f"Canales generados: {count}")
    print(f"Categorías: {len(set(categories.values()))}")
    print("Reproducción: URL HLS directa")
    print("Proxy jmp2.uk: no utilizado")
    print(f"Archivo: {OUTPUT}")
    print("--------------------------------")


if __name__ == "__main__":
    main()
