
import logging
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from curl_cffi import requests as curl_requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

OUTPUT_FILE = "rakuten_es.m3u"

API_BASE = "https://gizmo.rakuten.tv/v3"

CLASSIFICATION_ID = 5
COUNTRY = "es"
LOCALE = "es"

EPG_URL = (
    "https://raw.githubusercontent.com/"
    "ElTirillas/RakutenTV/main/Rakuten_ES_epg.xml"
)

ORIGIN = "https://www.rakuten.tv"
REFERER = "https://www.rakuten.tv/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
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

_thread_local = threading.local()

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
    # Rakuten valida también la huella TLS del navegador. requests estándar
    # puede recibir un 422 aunque los parámetros JSON sean correctos.
    # curl_cffi reproduce el handshake de Chrome, como la implementación
    # de referencia de VibraVid.
    session = curl_requests.Session(impersonate="chrome")

    session.headers.update({
        "Origin": ORIGIN,
        "Referer": REFERER,
        "Accept": "application/json, text/plain, */*",
    })

    return session


def get_session():
    if not hasattr(_thread_local, "session"):
        _thread_local.session = create_session()

    return _thread_local.session


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


def api_headers():
    return {
        "Origin": ORIGIN,
        "Referer": REFERER,
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
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
# OBTENER STREAM
# ============================================================

def get_channel_stream(channel):
    channel_id = channel.get("id")

    languages = (
        channel.get("labels", {})
        .get("languages", [])
    )

    # Rakuten espera un idioma de audio real; MIS se reserva para subtítulos.
    audio_language = "SPA"

    if languages:
        first_language = languages[0]

        if isinstance(first_language, dict):
            audio_language = first_language.get("id", "SPA")

    # Mantener la petición igual que la implementación pública de referencia
    # de Rakuten: no añadir identificadores de dispositivo ni campos opcionales.
    # El endpoint /avod/streamings puede fallar internamente si recibe metadatos
    # de dispositivo que no reconoce.
    params = api_params()
    params.update({
        "device_stream_audio_quality": "2.0",
        "device_stream_hdr_type": "NONE",
        "device_stream_video_quality": "FHD",
        "disable_dash_legacy_packages": False,
    })

    data = {
        "audio_language": audio_language,
        "audio_quality": "2.0",
        "classification_id": CLASSIFICATION_ID,
        "content_id": channel_id,
        "content_type": "live_channels",
        "device_serial": "not implemented",
        "player": "web:HLS-NONE:NONE",
        "strict_video_quality": False,
        "subtitle_language": "MIS",
        "video_type": "stream",
    }

    try:
        response = get_session().post(
            f"{API_BASE}/avod/streamings",
            params=params,
            headers=api_headers(),
            json=data,
            timeout=TIMEOUT,
        )

        response.raise_for_status()

        payload = response.json()

        stream_infos = (
            payload.get("data", {})
            .get("stream_infos", [])
        )

        for stream in stream_infos:
            url = stream.get("url")

            if url and ".m3u8" in url:
                return channel_id, url

        log.warning(
            "Sin URL HLS: %s",
            channel.get("title", channel_id)
        )

    except Exception as error:
        global _stream_debug_logged

        # Registrar el cuerpo y los parámetros de la primera respuesta fallida.
        # Así evitamos llenar el log con 127 errores idénticos.
        with _stream_debug_lock:
            if not _stream_debug_logged:
                _stream_debug_logged = True
                log.error(
                    "DIAGNÓSTICO RAKUTEN: HTTP %s para canal %s (%s)",
                    getattr(locals().get("response"), "status_code", "sin respuesta"),
                    channel_id,
                    channel.get("title", channel_id),
                )
                if locals().get("response") is not None:
                    log.error(
                        "DIAGNÓSTICO RAKUTEN: cuerpo de respuesta: %s",
                        locals()["response"].text[:5000],
                    )
                log.error(
                    "DIAGNÓSTICO RAKUTEN: parámetros enviados: %s",
                    params,
                )
                log.error(
                    "DIAGNÓSTICO RAKUTEN: JSON enviado: %s",
                    data,
                )

        log.warning(
            "Error obteniendo stream de %s: %s",
            channel.get("title", channel_id),
            error
        )

    return channel_id, None


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

    # Canales, categorías y EPG se consultan en paralelo.
    with ThreadPoolExecutor(max_workers=3) as executor:
        channels_future = executor.submit(get_live_channels)
        categories_future = executor.submit(get_categories)
        epg_future = executor.submit(get_epg_mapping)

        channels = channels_future.result()
        categories = categories_future.result()
        epg_mapping = epg_future.result()

    log.info(
        "Solicitando streams de %s canales...",
        len(channels)
    )

    streams = {}

    # Solicitudes de reproducción concurrentes.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(get_channel_stream, channel)
            for channel in channels
        ]

        completed = 0

        for future in as_completed(futures):
            channel_id, stream_url = future.result()

            if stream_url:
                streams[channel_id] = stream_url

            completed += 1

            if completed % 10 == 0 or completed == len(futures):
                log.info(
                    "Streams procesados: %s/%s",
                    completed,
                    len(futures)
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
