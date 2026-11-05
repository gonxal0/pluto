
import gzip
import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURACIÓN
# ============================================================

OUTPUT_FILE = "plex_es.m3u"

CATALOG_URL = (
    "https://github.com/matthuisman/i.mjh.nz/raw/"
    "refs/heads/master/Plex/.channels.json.gz"
)

EPG_URL = (
    "https://github.com/matthuisman/i.mjh.nz/raw/"
    "refs/heads/master/Plex/es.xml.gz"
)

TOKEN_URL = "https://clients.plex.tv/api/v2/users/anonymous"

PLAYBACK_BASE_URL = "https://epg.provider.plex.tv/library/parts"

REGION = "es"
TIMEOUT = (5, 15)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

log = logging.getLogger("plex-provider")


# ============================================================
# CATEGORÍAS DE PLEX
# ============================================================

CATEGORY_TRANSLATIONS = {
    "featured": "Destacados",
    "bingeworthy": "Para maratonear",
    "movies": "Películas",
    "true crime": "Crimen real",
    "truecrime": "Crimen real",
    "news": "Noticias",
    "sports": "Deportes",
    "reality": "Reality",
    "classics": "Clásicos",
    "adrenaline & sci-fi": "Acción y ciencia ficción",
    "adrenaline and sci-fi": "Acción y ciencia ficción",
    "comedy": "Comedia",
    "daytime tv & games": "Programas diurnos y concursos",
    "daytime tv and games": "Programas diurnos y concursos",
    "explore": "Explorar",
    "food, home & culture": "Cocina, hogar y cultura",
    "food home culture": "Cocina, hogar y cultura",
    "kids & family": "Niños y familia",
    "kids and family": "Niños y familia",
    "en español": "En español",
    "en espanol": "En español",
    "global": "Internacional",
    "music": "Música",
}

CATEGORY_ORDER = [
    "Destacados",
    "Para maratonear",
    "Películas",
    "Crimen real",
    "Noticias",
    "Deportes",
    "Reality",
    "Clásicos",
    "Acción y ciencia ficción",
    "Comedia",
    "Programas diurnos y concursos",
    "Explorar",
    "Cocina, hogar y cultura",
    "Niños y familia",
    "En español",
    "Internacional",
    "Música",
    "Otros",
]

# Clasificación orientativa cuando el catálogo no incluye categoría.
KEYWORD_RULES = [
    ("Noticias", [
        "news", "noticias", "cnn", "bbc news", "euronews",
        "nbc news", "cbs news", "sky news", "breaking"
    ]),
    ("Deportes", [
        "sport", "sports", "deportes", "football", "soccer",
        "nfl", "nba", "mlb", "tennis", "golf", "motorsport"
    ]),
    ("Películas", [
        "movie", "movies", "film", "films", "cinema",
        "cine", "action movies", "western"
    ]),
    ("Crimen real", [
        "true crime", "crime", "murder", "detective",
        "forensic", "police", "investigation"
    ]),
    ("Reality", [
        "reality", "real housewives", "dating", "wedding",
        "competition", "survival"
    ]),
    ("Clásicos", [
        "classic", "classics", "retro", "vintage", "oldies"
    ]),
    ("Acción y ciencia ficción", [
        "sci-fi", "scifi", "science fiction", "adventure",
        "action", "fantasy", "superhero"
    ]),
    ("Comedia", [
        "comedy", "comedies", "sitcom", "funny"
    ]),
    ("Programas diurnos y concursos", [
        "game show", "gameshow", "quiz", "daytime",
        "talk show", "talkshow"
    ]),
    ("Cocina, hogar y cultura", [
        "food", "cooking", "cuisine", "home", "garden",
        "travel", "culture", "lifestyle"
    ]),
    ("Niños y familia", [
        "kids", "children", "family", "cartoon", "animation",
        "junior", "preschool"
    ]),
    ("Música", [
        "music", "musical", "concert", "radio", "hits"
    ]),
]


# ============================================================
# HTTP
# ============================================================

def create_session():
    session = requests.Session()

    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=8,
        pool_maxsize=8,
    )

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    })

    return session


SESSION = create_session()


# ============================================================
# DESCARGA DEL CATÁLOGO
# ============================================================

def get_catalog():
    log.info("Descargando catálogo Plex...")

    response = SESSION.get(
        CATALOG_URL,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    content = response.content

    try:
        content = gzip.decompress(content)
    except gzip.BadGzipFile:
        pass

    data = json.loads(content.decode("utf-8"))

    channels = data.get("channels", {})

    if not isinstance(channels, dict) or not channels:
        raise RuntimeError("El catálogo Plex no contiene canales.")

    log.info("Canales encontrados en catálogo: %s", len(channels))

    return channels


# ============================================================
# TOKEN ANÓNIMO
# ============================================================

def get_anonymous_token():
    log.info("Solicitando token Plex...")

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

    response = SESSION.post(
        TOKEN_URL,
        headers=headers,
        params=params,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    token = response.json().get("authToken")

    if not token:
        raise RuntimeError("Plex no ha devuelto un token válido.")

    log.info("Token Plex obtenido correctamente.")

    return token


# ============================================================
# LIMPIEZA DE TEXTO
# ============================================================

def clean_text(value, default=""):
    if value is None:
        return default

    value = str(value)
    value = value.replace("\r", " ").replace("\n", " ")
    value = value.replace('"', "'")

    return re.sub(r"\s+", " ", value).strip()


def normalize(value):
    value = clean_text(value).lower()
    value = value.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", value).strip()


# ============================================================
# DETECCIÓN DE CATEGORÍAS
# ============================================================

def extract_category_values(channel):
    """
    Busca categorías en distintos formatos posibles del catálogo.
    """

    values = []

    fields = [
        "category",
        "categories",
        "group",
        "groups",
        "genre",
        "genres",
        "tags",
        "collection",
        "collections",
    ]

    for field in fields:
        value = channel.get(field)

        if isinstance(value, str):
            values.append(value)

        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    values.append(item)

                elif isinstance(item, dict):
                    for key in ("name", "title", "tag", "label"):
                        if item.get(key):
                            values.append(str(item[key]))

        elif isinstance(value, dict):
            for key in ("name", "title", "tag", "label"):
                if value.get(key):
                    values.append(str(value[key]))

    return values


def classify_channel(channel):
    """
    1. Utiliza la categoría original si está disponible.
    2. Si no existe, intenta deducirla por el nombre.
    3. Si no hay información suficiente, usa Otros.
    """

    categories = extract_category_values(channel)

    for raw_category in categories:
        normalized = normalize(raw_category)

        if normalized in CATEGORY_TRANSLATIONS:
            return CATEGORY_TRANSLATIONS[normalized]

    name = normalize(channel.get("name", ""))

    for category, keywords in KEYWORD_RULES:
        for keyword in keywords:
            if normalize(keyword) in name:
                return category

    # Detectar canales cuyo nombre indica claramente idioma español.
    if any(x in name for x in [
        "espanol", "español", "latino", "latina"
    ]):
        return "En español"

    return "Otros"


# ============================================================
# GENERACIÓN M3U
# ============================================================

def channel_sort_key(item):
    category, name, _ = item

    try:
        category_index = CATEGORY_ORDER.index(category)
    except ValueError:
        category_index = len(CATEGORY_ORDER)

    return category_index, name.lower()


def build_channel_url(channel_id, token):
    safe_id = quote(str(channel_id), safe="")

    return (
        f"{PLAYBACK_BASE_URL}/{safe_id}/"
        f"?X-Plex-Token={quote(token, safe='')}"
    )


def build_extinf(channel_id, channel, category):
    name = clean_text(channel.get("name"), str(channel_id))
    logo = clean_text(channel.get("logo", ""))

    # El ID del canal se conserva aunque no tenga EPG.
    tvg_id = clean_text(
        channel.get("tvg-id") or channel.get("epg_id") or channel_id
    )

    channel_number = clean_text(channel.get("chno", ""))

    attributes = [
        f'tvg-id="{tvg_id}"',
        f'tvg-name="{name}"',
        f'tvg-logo="{logo}"',
        f'group-title="{category}"',
    ]

    if channel_number:
        attributes.append(f'tvg-chno="{channel_number}"')

    return (
        f"#EXTINF:-1 {' '.join(attributes)},"
        f"{name}\n"
    )


def generate_playlist(channels, token):
    log.info("Preparando canales de España...")

    entries = []
    seen_ids = set()

    for channel_id, channel in channels.items():

        regions = channel.get("regions", [])

        if REGION not in regions:
            continue

        if channel_id in seen_ids:
            continue

        name = clean_text(channel.get("name"))

        if not name:
            continue

        category = classify_channel(channel)

        extinf = build_extinf(
            channel_id,
            channel,
            category
        )

        stream_url = build_channel_url(
            channel_id,
            token
        )

        entries.append((
            category,
            name,
            extinf + stream_url + "\n"
        ))

        seen_ids.add(channel_id)

    if not entries:
        raise RuntimeError(
            "No se han encontrado canales para España."
        )

    entries.sort(key=channel_sort_key)

    output = [
        f'#EXTM3U url-tvg="{EPG_URL}"\n'
    ]

    current_category = None

    for category, name, content in entries:
        if category != current_category:
            current_category = category
            log.info("Categoría: %s", category)

        output.append(content)

    playlist = "".join(output)

    log.info("Canales españoles generados: %s", len(entries))

    return playlist, len(entries)


# ============================================================
# GUARDADO Y VALIDACIÓN
# ============================================================

def save_playlist(content):
    temporary_file = OUTPUT_FILE + ".tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8",
        newline="\n"
    ) as file:
        file.write(content)

    os.replace(temporary_file, OUTPUT_FILE)

    log.info("Lista guardada: %s", OUTPUT_FILE)


def validate_playlist(expected_channels):
    if not os.path.exists(OUTPUT_FILE):
        raise RuntimeError("No se ha generado el archivo M3U.")

    with open(
        OUTPUT_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        content = file.read()

    extinf_count = content.count("#EXTINF:")
    url_count = sum(
        1 for line in content.splitlines()
        if line.startswith("https://epg.provider.plex.tv/")
    )

    if not content.startswith("#EXTM3U"):
        raise RuntimeError("Cabecera M3U incorrecta.")

    if extinf_count != expected_channels:
        raise RuntimeError("Número de canales incorrecto.")

    if url_count != expected_channels:
        raise RuntimeError("Hay canales sin URL de reproducción.")

    log.info(
        "Validación correcta: %s canales.",
        expected_channels
    )


# ============================================================
# MAIN
# ============================================================

def main():
    log.info("====================================")
    log.info("      GENERADOR PLEX ESPAÑA")
    log.info("====================================")

    # Catálogo y token se obtienen simultáneamente.
    with ThreadPoolExecutor(max_workers=2) as executor:
        catalog_future = executor.submit(get_catalog)
        token_future = executor.submit(get_anonymous_token)

        channels = catalog_future.result()
        token = token_future.result()

    playlist, total = generate_playlist(
        channels,
        token
    )

    save_playlist(playlist)

    validate_playlist(total)

    log.info("Generación Plex finalizada correctamente.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Error generando la lista Plex.")
        raise
