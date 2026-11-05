
import logging
import os
import re
import time
import threading
import unicodedata
import xml.etree.ElementTree as ET

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

OUTPUT_FILE = "rakuten_es.m3u"

API_BASE = "https://gizmo.rakuten.tv/v3"

CLASSIFICATION_ID = 5  # fallback si Rakuten no devuelve la clasificación
COUNTRY = "es"
LOCALE = "es"

EPG_URL = (
    "https://raw.githubusercontent.com/"
    "ElTirillas/RakutenTV/main/Rakuten_ES_epg.xml"
)

ORIGIN = "https://www.rakuten.tv"
REFERER = "https://www.rakuten.tv/"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

TIMEOUT = (8, 20)
MAX_WORKERS = 8
PAGE_SIZE = 100
MAX_PAGES = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

log = logging.getLogger("rakuten-provider")

_session = None


_stream_debug_lock = threading.Lock()
_stream_debug_logged = False


# ============================================================
# CATEGORÍAS
# ============================================================

CATEGORY_TRANSLATIONS = {
    "action": "Acción",
    "action movies": "Películas de acción",
    "animation": "Animación",
    "anime": "Anime",
    "children": "Infantil",
    "kids": "Infantil",
    "comedy": "Comedia",
    "comedies": "Comedia",
    "documentary": "Documentales",
    "documentaries": "Documentales",
    "drama": "Drama",
    "entertainment": "Entretenimiento",
    "family": "Familiar",
    "films": "Películas",
    "film": "Películas",
    "horror": "Terror",
    "lifestyle": "Estilo de vida",
    "movies": "Películas",
    "music": "Música",
    "news": "Noticias",
    "reality": "Reality",
    "series": "Series",
    "sports": "Deportes",
    "thriller": "Suspense",
    "western": "Western",
    "no_category": "Otros",
    "otros": "Otros",
    "peliculas": "Películas",
    "películas": "Películas",
    "deportes": "Deportes",
    "noticias": "Noticias",
    "infantil": "Infantil",
    "documentales": "Documentales",
    "musica": "Música",
    "música": "Música",
    "entretenimiento": "Entretenimiento",
    "terror": "Terror",
    "series": "Series",
    "comedia": "Comedia",
    "familiar": "Familiar",
    "accion": "Acción",
    "acción": "Acción",
}

CATEGORY_ORDER = [
    "Destacados",
    "Películas",
    "Películas de acción",
    "Series",
    "Documentales",
    "Noticias",
    "Deportes",
    "Comedia",
    "Drama",
    "Acción",
    "Suspense",
    "Terror",
    "Infantil",
    "Animación",
    "Anime",
    "Música",
    "Reality",
    "Entretenimiento",
    "Estilo de vida",
    "Familiar",
    "Western",
    "Otros",
]


# ============================================================
# SESIONES HTTP
# ============================================================

def create_session():
    # Reutilizar una única sesión HTTP para conservar cookies/estado de Rakuten.
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Origin": ORIGIN,
        "Referer": REFERER,
    })


    return session


def get_session():
    global _session
    if _session is None:
        _session = create_session()
    return _session


# ============================================================
# NORMALIZACIÓN
# ============================================================

def clean_text(value, default=""):
    if value is None:
        return default

    value = str(value)
    value = value.replace("\r", " ")
    value = value.replace("\n", " ")
    value = value.replace('"', "'")

    return re.sub(r"\s+", " ", value).strip()


def normalize(value):
    value = clean_text(value).lower()

    value = unicodedata.normalize(
        "NFD",
        value
    )

    value = "".join(
        char for char in value
        if unicodedata.category(char) != "Mn"
    )

    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def translate_category(value):
    original = clean_text(value, "Otros")
    key = normalize(original)

    return CATEGORY_TRANSLATIONS.get(
        key,
        original if original else "Otros"
    )


# ============================================================
# PARÁMETROS API
# ============================================================

def api_params():
    return {
        "classification_id": CLASSIFICATION_ID,
        "device_identifier": "web",
        "locale": LOCALE,
        "market_code": COUNTRY,
    }


def get_classification_id():
    """
    Obtiene la clasificación predeterminada del mercado español desde Rakuten.
    Evita depender de un ID fijo si la API lo ha cambiado.
    """
    try:
        response = get_session().get(
            f"{API_BASE}/classifications",
            params={
                "device_identifier": "web",
                "locale": LOCALE,
                "market_code": COUNTRY,
            },
            headers=api_headers(),
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        entries = response.json().get("data") or []
        chosen = next((item for item in entries if item.get("default")), None)
        if chosen is None and entries:
            chosen = entries[-1]
        if chosen and chosen.get("numerical_id") is not None:
            value = int(chosen["numerical_id"])
            log.info("Clasificación Rakuten España detectada: %s", value)
            return value
    except Exception as error:
        log.warning("No se pudo consultar /classifications: %s", error)

    log.warning("Se utilizará la clasificación de respaldo: %s", CLASSIFICATION_ID)
    return CLASSIFICATION_ID


def api_headers():
    # La IP de salida real la determina la conexión/proxy, no estas cabeceras.
    return {
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": USER_AGENT,
    }


# ============================================================
# OBTENER CANALES
# ============================================================

def get_live_channels():
    log.info("Consultando canales de Rakuten España...")

    channels = []
    seen_ids = set()

    for page in range(1, MAX_PAGES + 1):
        params = {
            "classification_id": CLASSIFICATION_ID,
            "device_identifier": "web",
            "locale": LOCALE,
            "market_code": COUNTRY,
            "page": page,
        }

        response = get_session().get(
            f"{API_BASE}/live_channels",
            params=params,
            headers=api_headers(),
            timeout=TIMEOUT,
        )

        if response.status_code != 200:
            log.error(
                "Error consultando página %s: HTTP %s",
                page,
                response.status_code,
            )
            log.error(
                "Respuesta Rakuten: %s",
                response.text[:3000],
            )
            response.raise_for_status()

        payload = response.json()
        page_channels = payload.get("data", [])

        if not isinstance(page_channels, list):
            raise RuntimeError(
                f"Formato inesperado en página {page}: "
                f"{str(payload)[:2000]}"
            )

        if not page_channels:
            log.info("No hay más canales en la página %s.", page)
            break

        new_channels = 0

        for channel in page_channels:
            channel_id = channel.get("id")

            if not channel_id or channel_id in seen_ids:
                continue

            seen_ids.add(channel_id)
            channels.append(channel)
            new_channels += 1

        log.info(
            "Página %s: %s canales recibidos, %s nuevos.",
            page,
            len(page_channels),
            new_channels,
        )

        if new_channels == 0:
            break

    if not channels:
        raise RuntimeError(
            "Rakuten no ha devuelto ningún canal."
        )

    log.info(
        "Total de canales únicos recibidos: %s",
        len(channels),
    )

    return channels

# ============================================================
# OBTENER CATEGORÍAS
# ============================================================

def get_categories():
    log.info("Consultando categorías de Rakuten...")

    response = get_session().get(
        f"{API_BASE}/live_channel_categories",
        params=api_params(),
        headers=api_headers(),
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    payload = response.json()

    mapping = {}

    for category in payload.get("data", []):
        category_name = category.get("name", "Otros")

        for channel_id in category.get("live_channels", []):
            mapping[channel_id] = translate_category(
                category_name
            )

    log.info(
        "Categorías asociadas a canales: %s",
        len(mapping)
    )

    return mapping


# ============================================================
# EPG XMLTV
# ============================================================

def get_epg_mapping():
    log.info("Descargando EPG española de Rakuten...")

    try:
        response = get_session().get(
            EPG_URL,
            timeout=(8, 30)
        )

        response.raise_for_status()

        root = ET.fromstring(response.content)

        mapping = {}

        for channel in root.findall("channel"):
            channel_id = channel.get("id")

            if not channel_id:
                continue

            for display_name in channel.findall("display-name"):
                name = normalize(display_name.text)

                if name:
                    mapping.setdefault(name, channel_id)

        log.info(
            "Canales identificados en EPG: %s",
            len(mapping)
        )

        return mapping

    except Exception as error:
        log.warning(
            "No se pudo descargar o leer la EPG: %s",
            error
        )

        return {}


def match_epg_id(channel, epg_mapping):
    """
    Intenta asociar el nombre del canal de la API
    con el display-name del XMLTV.
    """

    title = normalize(channel.get("title", ""))

    if title in epg_mapping:
        return epg_mapping[title]

    # Prueba quitando el prefijo Rakuten TV.
    simplified = re.sub(
        r"^rakuten tv ",
        "",
        title
    ).strip()

    if simplified in epg_mapping:
        return epg_mapping[simplified]

    return clean_text(channel.get("id"))


# ============================================================
# LOGOTIPOS
# ============================================================

def find_logo(channel):
    images = channel.get("images", {})

    if isinstance(images, dict):
        preferred_keys = [
            "logo",
            "channel_logo",
            "artwork",
            "thumbnail",
            "poster",
        ]

        for key in preferred_keys:
            value = images.get(key)

            if isinstance(value, str) and value.startswith("http"):
                return value

            if isinstance(value, dict):
                for subkey in ("url", "src", "href"):
                    url = value.get(subkey)

                    if isinstance(url, str) and url.startswith("http"):
                        return url

    for key in ("logo", "image", "image_url"):
        value = channel.get(key)

        if isinstance(value, str) and value.startswith("http"):
            return value

    return ""


# ============================================================
# OBTENER STREAM HLS DIRECTO
# ============================================================

# Patrón confirmado con dos canales españoles (Sci-Fi y Thriller).
# El hash y la plantilla de ruta son comunes en ambas capturas.
DIRECT_HLS_HASH = "0547f18649bd788bec7b67b746e47670f558b6b2"
DIRECT_HLS_SUFFIX = "-rakuten-tv-es.fast.rakuten.tv"


def channel_slug_candidates(channel):
    """
    Prioriza el identificador técnico del canal de Rakuten.
    El título visible no siempre coincide con el subdominio HLS.
    """
    values = []
    for key in ("streaming_slug", "slug", "seo_slug", "channel_slug", "url_slug", "id"):
        value = channel.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())

    candidates = []
    seen = set()

    for value in values:
        if value.startswith(("http://", "https://")):
            value = value.split("?", 1)[0].rstrip("/").split("/")[-1]

        value = re.sub(r"-rakuten-tv-es$", "", value, flags=re.IGNORECASE)
        value = re.sub(r"-rakuten-tv$", "", value, flags=re.IGNORECASE)
        slug = normalize(value).replace(" ", "-").strip("-")

        if slug and slug not in seen:
            seen.add(slug)
            candidates.append(slug)

    return candidates


def get_channel_stream(channel):
    """
    Construye el HLS directo usando numerical_id (no el ID textual).
    Valida la playlist antes de devolverla.
    """
    content_id = str(channel.get("id") or "").strip()
    playback_id = str(channel.get("numerical_id") or "").strip()
    title = clean_text(channel.get("title"), content_id)

    if not content_id or not playback_id or not playback_id.isdigit():
        log.warning(
            "Falta numerical_id válido para %s (id=%s, numerical_id=%s).",
            title,
            content_id,
            playback_id or "vacío",
        )
        return content_id, None

    slugs = channel_slug_candidates(channel)
    if not slugs:
        log.warning("No se pudo obtener el slug técnico de %s.", title)
        return content_id, None

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        "Origin": ORIGIN,
        "Referer": REFERER,
    }

    for slug in slugs:
        url = (
            f"https://{slug}{DIRECT_HLS_SUFFIX}/v1/master/"
            f"{DIRECT_HLS_HASH}/production-hls-aes128-rtv-"
            f"{playback_id}-playout/master.m3u8"
        )

        response = None
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=TIMEOUT,
                stream=True,
                allow_redirects=True,
            )
            sample = next(response.iter_content(chunk_size=2048), b"")

            if response.status_code == 200 and b"#EXTM3U" in sample:
                log.info(
                    "HLS directo válido: %s | id=%s | numerical_id=%s | slug=%s",
                    title,
                    content_id,
                    playback_id,
                    slug,
                )
                return content_id, response.url.split("?", 1)[0]

            log.debug(
                "HLS no válido para %s con slug %s: HTTP %s, tipo=%s",
                title,
                slug,
                response.status_code,
                response.headers.get("Content-Type", "desconocido"),
            )
        except requests.RequestException as error:
            log.debug("Error probando HLS de %s (%s): %s", title, slug, error)
        finally:
            if response is not None:
                response.close()

    log.warning(
        "No se encontró HLS para %s (numerical_id=%s). Slugs probados: %s",
        title,
        playback_id,
        ", ".join(slugs),
    )
    return content_id, None



# ============================================================
# GENERAR LISTA
# ============================================================

def channel_sort_key(item):
    category = item["category"]
    title = item["title"]

    try:
        category_index = CATEGORY_ORDER.index(category)
    except ValueError:
        category_index = len(CATEGORY_ORDER)

    return category_index, title.lower()


def build_extinf(channel, category, epg_mapping):
    channel_id = clean_text(channel.get("id"))
    title = clean_text(channel.get("title"), channel_id)

    numerical_id = channel.get("channel_number", "")

    logo = clean_text(find_logo(channel))

    epg_id = match_epg_id(
        channel,
        epg_mapping
    )

    attributes = [
        f'tvg-id="{clean_text(epg_id)}"',
        f'tvg-name="{title}"',
        f'tvg-logo="{logo}"',
        f'tvg-country="ES"',
        f'group-title="{clean_text(category)}"',
    ]

    try:
        number = int(numerical_id)

        if number >= 0:
            attributes.insert(
                0,
                f'tvg-chno="{number}"'
            )
    except (TypeError, ValueError):
        pass

    return (
        f"#EXTINF:-1 {' '.join(attributes)},"
        f"{title}\n"
    )


def generate_playlist(
    channels,
    categories,
    streams,
    epg_mapping
):
    entries = []

    epg_matches = 0
    missing_streams = 0

    for channel in channels:
        channel_id = channel.get("id")
        stream_url = streams.get(channel_id)

        if not stream_url:
            missing_streams += 1
            continue

        category = categories.get(
            channel_id,
            "Otros"
        )

        if not category:
            category = "Otros"

        if match_epg_id(channel, epg_mapping) != channel_id:
            epg_matches += 1

        extinf = build_extinf(
            channel,
            category,
            epg_mapping
        )

        entries.append({
            "category": category,
            "title": clean_text(channel.get("title")),
            "content": extinf + stream_url + "\n",
        })

    entries.sort(key=channel_sort_key)

    if not entries:
        raise RuntimeError(
            "No se ha podido obtener ningún canal reproducible."
        )

    output = [
        f'#EXTM3U url-tvg="{EPG_URL}"\n'
    ]

    current_category = None

    for entry in entries:
        if entry["category"] != current_category:
            current_category = entry["category"]

            log.info(
                "Categoría: %s",
                current_category
            )

        output.append(entry["content"])

    log.info("Canales con stream: %s", len(entries))
    log.info("Canales sin stream: %s", missing_streams)
    log.info("Canales con coincidencia EPG: %s", epg_matches)

    return "".join(output), len(entries)


# ============================================================
# GUARDAR Y VALIDAR
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
    with open(
        OUTPUT_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        content = file.read()

    extinf_count = content.count("#EXTINF:")

    urls = [
        line for line in content.splitlines()
        if line.startswith("https://")
    ]

    if not content.startswith("#EXTM3U"):
        raise RuntimeError("Cabecera M3U incorrecta.")

    if extinf_count != expected_channels:
        raise RuntimeError("Número de canales incorrecto.")

    if len(urls) != expected_channels:
        raise RuntimeError("Hay canales sin URL de reproducción.")

    log.info(
        "Validación correcta: %s canales.",
        expected_channels
    )


# ============================================================
# MAIN
# ============================================================

def main():
    start = time.time()

    log.info("========================================")
    log.info("       GENERADOR RAKUTEN TV ESPAÑA")
    log.info("========================================")

    # Mercado español. Consultar la clasificación vigente a la propia API.
    global CLASSIFICATION_ID
    CLASSIFICATION_ID = get_classification_id()

    channels = get_live_channels()
    categories = get_categories()
    epg_mapping = get_epg_mapping()

    log.info(
        "Solicitando streams de %s canales...",
        len(channels)
    )

    streams = {}

    # Rakuten/OpenCockpit resuelve los streams de uno en uno.
    # Mantener la misma secuencia evita diferencias por concurrencia y
    # permite comparar el comportamiento del endpoint con esa implementación.
    for completed, channel in enumerate(channels, start=1):
        channel_id, stream_url = get_channel_stream(channel)

        if stream_url:
            streams[channel_id] = stream_url

        if completed % 10 == 0 or completed == len(channels):
            log.info(
                "Streams procesados: %s/%s",
                completed,
                len(channels)
            )

    playlist, total = generate_playlist(
        channels,
        categories,
        streams,
        epg_mapping
    )

    save_playlist(playlist)
    validate_playlist(total)

    elapsed = time.time() - start

    log.info(
        "Rakuten España finalizado en %.1f segundos.",
        elapsed
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Error generando Rakuten España.")
        raise
