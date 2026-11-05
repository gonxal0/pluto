
import gzip
import json
import os
import uuid
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURACIÓN
# ============================================================

OUTPUT = "plex_es.m3u"

CATALOG_URL = (
    "https://github.com/matthuisman/i.mjh.nz/raw/"
    "refs/heads/master/Plex/.channels.json.gz"
)

EPG_URL = (
    "https://github.com/matthuisman/i.mjh.nz/raw/"
    "refs/heads/master/Plex/es.xml.gz"
)

ANONYMOUS_TOKEN_URL = (
    "https://clients.plex.tv/api/v2/users/anonymous"
)

PLAYBACK_BASE_URL = (
    "https://epg.provider.plex.tv/library/parts"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)


# ============================================================
# SESIÓN HTTP
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": USER_AGENT,
})

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
    pool_connections=5,
    pool_maxsize=5,
)

session.mount("https://", adapter)


# ============================================================
# DESCARGA DEL CATÁLOGO
# ============================================================

def get_catalog():
    print("Descargando catálogo de Plex...")

    response = session.get(
        CATALOG_URL,
        timeout=(10, 30),
    )

    response.raise_for_status()

    try:
        content = gzip.decompress(response.content)
    except gzip.BadGzipFile:
        content = response.content

    data = json.loads(content)

    channels = data.get("channels", {})

    if not isinstance(channels, dict) or not channels:
        raise RuntimeError(
            "El catálogo de Plex no contiene canales."
        )

    print(f"Canales en catálogo global: {len(channels)}")

    return channels


# ============================================================
# AUTENTICACIÓN ANÓNIMA PLEX
# ============================================================

def get_anonymous_token():
    print("Solicitando token anónimo de Plex...")

    client_id = uuid.uuid4().hex

    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-Plex-Product": "Plex Web",
        "X-Plex-Version": "4.150.0",
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Platform": "Web",
    }

    params = {
        "X-Plex-Product": "Plex Web",
        "X-Plex-Client-Identifier": client_id,
    }

    response = requests.post(
        ANONYMOUS_TOKEN_URL,
        headers=headers,
        params=params,
        timeout=(10, 20),
    )

    response.raise_for_status()

    token = response.json().get("authToken")

    if not token:
        raise RuntimeError(
            "Plex no ha devuelto un token anónimo."
        )

    print("Token de Plex obtenido correctamente.")

    return token


# ============================================================
# GENERACIÓN M3U
# ============================================================

def clean(value):
    return str(value or "").replace('"', "'")


def format_extinf(channel_id, channel):
    name = clean(channel.get("name"))
    logo = clean(channel.get("logo", ""))
    chno = channel.get("chno", "")

    if not str(chno).isdigit():
        chno = ""

    attrs = {
        "channel-id": channel_id,
        "tvg-id": channel_id,
        "tvg-chno": chno,
        "tvg-name": name,
        "tvg-logo": logo,
        "group-title": "Plex España",
    }

    metadata = " ".join(
        f'{key}="{clean(value)}"'
        for key, value in attrs.items()
    )

    display_name = name.replace(",", "")

    return (
        f"#EXTINF:-1 {metadata},{display_name}\n"
    )


def build_stream_url(channel_id, token):
    return (
        f"{PLAYBACK_BASE_URL}/"
        f"{quote(str(channel_id), safe='')}/"
        f"?X-Plex-Token={quote(token, safe='')}"
    )


def generate(channels, token):
    print("Filtrando canales de España...")

    selected = []

    for channel_id, channel in channels.items():

        regions = channel.get("regions", [])

        if isinstance(regions, str):
            regions = [regions]

        regions = {
            str(region).lower()
            for region in regions
        }

        if "es" not in regions:
            continue

        if not channel.get("name"):
            continue

        selected.append((channel_id, channel))

    selected.sort(
        key=lambda item: item[1].get(
            "name", ""
        ).lower()
    )

    if not selected:
        raise RuntimeError(
            "No hay canales de Plex disponibles para España."
        )

    lines = [
        f'#EXTM3U url-tvg="{EPG_URL}"\n'
    ]

    for channel_id, channel in selected:

        lines.append(
            format_extinf(channel_id, channel)
        )

        lines.append(
            build_stream_url(channel_id, token) + "\n"
        )

    content = "".join(lines)

    if content.count("#EXTINF:") != len(selected):
        raise RuntimeError(
            "El número de entradas M3U no coincide."
        )

    if "X-Plex-Token=" not in content:
        raise RuntimeError(
            "No se han generado URLs con token Plex."
        )

    return content, len(selected)


# ============================================================
# VALIDACIÓN
# ============================================================

def validate(content, count):
    if not content.startswith("#EXTM3U"):
        raise RuntimeError("Cabecera M3U incorrecta.")

    if count == 0:
        raise RuntimeError("Playlist vacía.")

    if content.count("#EXTINF:") != count:
        raise RuntimeError("Número de canales incorrecto.")

    if content.count("X-Plex-Token=") != count:
        raise RuntimeError(
            "No todas las URLs tienen token Plex."
        )


# ============================================================
# ESCRITURA SEGURA
# ============================================================

def save_playlist(content):
    temp_file = OUTPUT + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as file:
        file.write(content)

    os.replace(temp_file, OUTPUT)


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("================================")
    print("       PLEX TV ESPAÑA")
    print("================================")

    channels = get_catalog()

    token = get_anonymous_token()

    content, count = generate(
        channels,
        token,
    )

    validate(content, count)

    save_playlist(content)

    print("--------------------------------")
    print(f"Canales España: {count}")
    print(f"Archivo: {OUTPUT}")
    print("EPG: Plex España")
    print("Autenticación: token anónimo")
    print("--------------------------------")
    print("Generación completada.")


if __name__ == "__main__":
    main()
