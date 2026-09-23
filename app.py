# ============================================================
# VERIFICAR REF TECNICA - BOEING TOOLBOX
# ============================================================
# VERSAO: API AUTENTICADA PELO NAVEGADOR + DOWNLOAD DIRETO
#
# FLUXO:
#   1. Google Sheets (Service Account)
#   2. Login Boeing
#   3. Microsoft login
#   4. MFA Microsoft manual
#   5. Login with Authy
#   6. Pesquisar PNs da aba Boeing, um por vez
#   7. A propria pagina /results dispara a API oficial
#      /search/data-unit-contents com todos os headers/token
#   8. Selecionar documento na prioridade:
#        CMM -> GPSD -> IPL -> AMM
#   9. Dentro do mesmo tipo, selecionar publicacao/revisao mais recente
#  10. Abrir DocumentViewer apenas para descobrir o contentObjectId
#      / URL contentFile gerada pela aplicacao
#  11. Baixar o PDF diretamente via requests autenticado
#  12. Salvar tudo em uma unica pasta downloads/
#  13. Atualizar STATUS / DETALHE_STATUS
#
# IMPORTANTE:
#   - Nao existe chamada fetch manual com CSRF inventado.
#   - A busca eh disparada pelo proprio Angular do Boeing Toolbox.
#   - O token X-XSRF-TOKEN e X-Auth-Token sao capturados da
#     requisicao oficial do navegador.
#   - O PDF eh baixado pela API contentFile, sem depender do viewer.
# ============================================================

import os
import sys
import re
import json
import time
import socket
import shutil
import logging
import configparser
from pathlib import Path
from datetime import datetime
from getpass import getpass
from urllib.parse import quote, urljoin, urlparse

import requests
import urllib3
import gspread
from google.oauth2 import service_account

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ============================================================
# BASE / CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config_boeing.ini"


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"config_boeing.ini nao encontrado: {CONFIG_FILE}"
        )

    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(CONFIG_FILE, encoding="utf-8")

    required = [
        "BOEING",
        "GOOGLE",
        "SELENIUM",
        "BOEING_API",
        "PROCESSING",
        "PATHS",
    ]

    missing = [section for section in required if section not in cfg]
    if missing:
        raise RuntimeError(
            "Secoes ausentes no config_boeing.ini: "
            + ", ".join(missing)
        )

    return cfg


CONFIG = load_config()


# ============================================================
# CONFIG - BOEING
# ============================================================

BOEING_HOME_URL = CONFIG["BOEING"]["home_url"].strip()
BOEING_EMAIL = CONFIG["BOEING"]["email"].strip()
BOEING_PASSWORD = CONFIG["BOEING"].get("password", "")


# ============================================================
# CONFIG - GOOGLE
# ============================================================

GOOGLE_CREDENTIALS = Path(
    CONFIG["GOOGLE"].get("credentials_file", "credentials.json")
)

if not GOOGLE_CREDENTIALS.is_absolute():
    GOOGLE_CREDENTIALS = BASE_DIR / GOOGLE_CREDENTIALS

GOOGLE_SHEET_URL = CONFIG["GOOGLE"]["sheet_url"].strip()
GOOGLE_SHEET_TAB = CONFIG["GOOGLE"].get("sheet_tab", "Boeing").strip()


# ============================================================
# CONFIG - SELENIUM
# ============================================================

HEADLESS = CONFIG["SELENIUM"].getboolean("headless", fallback=False)
PAGE_LOAD_STRATEGY = CONFIG["SELENIUM"].get(
    "page_load_strategy", "none"
).strip()
PAGE_TIMEOUT = CONFIG["SELENIUM"].getint("page_timeout", fallback=30)
ELEMENT_TIMEOUT = CONFIG["SELENIUM"].getint(
    "element_timeout", fallback=60
)
MICROSOFT_2FA_TIMEOUT = CONFIG["SELENIUM"].getint(
    "microsoft_2fa_timeout", fallback=600
)
AVIATION_ID_TIMEOUT = CONFIG["SELENIUM"].getint(
    "aviation_id_timeout", fallback=120
)
TOOLBOX_READY_TIMEOUT = CONFIG["SELENIUM"].getint(
    "toolbox_ready_timeout", fallback=600
)
MAXIMIZE_WINDOW = CONFIG["SELENIUM"].getboolean(
    "maximize_window", fallback=True
)
IGNORE_CERTIFICATE_ERRORS = CONFIG["SELENIUM"].getboolean(
    "ignore_certificate_errors", fallback=True
)
POST_LOGIN_WAIT = CONFIG["SELENIUM"].getfloat(
    "post_login_wait", fallback=2
)


# ============================================================
# CONFIG - API
# ============================================================

SEARCH_API_PATH = CONFIG["BOEING_API"].get(
    "search_path", "/search/data-unit-contents"
).strip()

CONTENT_FILE_PATH = CONFIG["BOEING_API"].get(
    "content_file_path",
    "/product/fileRepo/contentFile/{content_object_id}",
).strip()

SEARCH_SIZE = CONFIG["BOEING_API"].getint("search_size", fallback=50)
SEARCH_EXACT = CONFIG["BOEING_API"].getboolean(
    "exact_match", fallback=True
)


# ============================================================
# CONFIG - PROCESSAMENTO
# ============================================================

UPDATE_SHEET = CONFIG["PROCESSING"].getboolean(
    "update_sheet", fallback=True
)

PROCESS_ONLY_BLANK_STATUS = CONFIG["PROCESSING"].getboolean(
    "process_only_blank_status", fallback=True
)

RETRY_STATUSES = {
    item.strip().upper()
    for item in CONFIG["PROCESSING"].get(
        "retry_statuses",
        "ERRO,SEM PDF",
    ).split(",")
    if item.strip()
}

MAX_ITEMS = CONFIG["PROCESSING"].getint(
    "max_items", fallback=0
)

DELAY_BETWEEN_ITEMS = CONFIG["PROCESSING"].getfloat(
    "delay_between_items", fallback=0.3
)

DOWNLOAD_TIMEOUT = CONFIG["PROCESSING"].getint(
    "download_timeout", fallback=300
)

UPDATE_LINK_PDF = CONFIG["PROCESSING"].getboolean(
    "update_link_pdf", fallback=False
)


# ============================================================
# PATHS
# ============================================================

DOWNLOAD_DIR = Path(
    CONFIG["PATHS"].get("download_dir", "downloads")
)

if not DOWNLOAD_DIR.is_absolute():
    DOWNLOAD_DIR = BASE_DIR / DOWNLOAD_DIR

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)

LOG_FILE = Path(
    CONFIG["PATHS"].get("log_file", "boeing_automacao.log")
)

if not LOG_FILE.is_absolute():
    LOG_FILE = BASE_DIR / LOG_FILE


# ============================================================
# LOGGING
# ============================================================

def log(status, message):
    timestamp = time.strftime("%H:%M:%S")
    line = f"{timestamp} | {status:<7} | {message}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def log_exception(context, exc):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            import traceback
            fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n")
            fh.write(traceback.format_exc())
            fh.write("\n")
    except Exception:
        pass


# ============================================================
# UTILS
# ============================================================

def normalize_text(value):
    return str(value or "").strip().upper()


def normalize_pn(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def safe_filename(value):
    value = str(value or "").strip()
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .") or "arquivo"


def parse_date(value):
    if not value:
        return datetime.min

    text = str(value).strip()

    formats = [
        "%d %b %Y",
        "%d %B %Y",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    return datetime.min


def revision_number(value):
    if value is None:
        return -1

    match = re.search(r"\d+", str(value))
    if not match:
        return -1

    try:
        return int(match.group())
    except ValueError:
        return -1


def first_value(obj, keys, default=""):
    wanted = {str(k).lower() for k in keys}

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in wanted and item not in (None, ""):
                    return item
                found = walk(item)
                if found not in (None, ""):
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found not in (None, ""):
                    return found
        return None

    found = walk(obj)
    return default if found in (None, "") else found


def is_pdf_result(result):
    candidates = [
        result.get("productFormat"),
        result.get("productFormatLabel"),
        result.get("displayProductFormatLabel"),
        result.get("format"),
        result.get("mimeType"),
        result.get("contentType"),
        result.get("fileType"),
    ]

    if any(
        "PDF" in normalize_text(value)
        for value in candidates
        if value is not None
    ):
        return True

    # Fallback para pequenas variacoes de payload.
    serialized = normalize_text(
        json.dumps(result, ensure_ascii=False)
    )
    return bool(
        re.search(
            r'"(?:productformat|format|mimetype|contenttype|filetype)"\s*:\s*"[^"]*PDF',
            serialized,
        )
    )


def document_type(result):
    candidates = [
        result.get("displayDocType"),
        result.get("productType"),
        result.get("documentTypeAbbrAndExpansion"),
        result.get("documentType"),
        result.get("docType"),
        result.get("SPI_DISPLAY_DOC_TYPE"),
    ]

    for value in candidates:
        text = normalize_text(value)
        if not text:
            continue

        for known in ["CMM", "GPSD", "IPL", "AMM"]:
            if (
                text == known
                or text.startswith(known + " ")
                or text.startswith(known + "-")
                or re.search(rf"\b{known}\b", text)
            ):
                return known

        return text

    return ""


def is_accessible(result):
    value = result.get("isAccessible")
    if value not in (None, ""):
        return str(value).lower() in {"1", "true", "yes"}

    displayable = result.get("isDisplayable")
    if displayable not in (None, ""):
        return str(displayable).lower() in {"1", "true", "yes"}

    return True


def is_not_latest(result):
    value = result.get("notLatest")
    if value in (None, ""):
        return False
    return str(value).lower() in {"1", "true", "yes"}


def unique_preserve(items):
    result = []
    seen = set()

    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)

    return result


# ============================================================
# CHROME
# ============================================================

def find_chrome():
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return shutil.which("chrome")


def create_driver():
    chrome_binary = find_chrome()
    if not chrome_binary:
        raise RuntimeError("Google Chrome nao encontrado.")

    options = Options()
    options.page_load_strategy = PAGE_LOAD_STRATEGY
    options.binary_location = chrome_binary

    if HEADLESS:
        options.add_argument("--headless=new")

    if MAXIMIZE_WINDOW:
        options.add_argument("--start-maximized")

    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")

    if IGNORE_CERTIFICATE_ERRORS:
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--allow-insecure-localhost")

    options.set_capability(
        "goog:loggingPrefs",
        {
            "performance": "ALL",
            "browser": "ALL",
        },
    )

    driver = webdriver.Chrome(
        service=Service(),
        options=options,
    )

    driver.set_page_load_timeout(PAGE_TIMEOUT)
    driver.set_script_timeout(PAGE_TIMEOUT)

    driver.execute_cdp_cmd(
        "Network.enable",
        {
            "maxTotalBufferSize": 100000000,
            "maxResourceBufferSize": 50000000,
            "maxPostDataSize": 10000000,
        },
    )

    return driver


# ============================================================
# CLICK
# ============================================================

def click_element(driver, element):
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});",
            element,
        )
    except Exception:
        pass

    try:
        element.click()
    except Exception:
        driver.execute_script(
            "arguments[0].click();",
            element,
        )


# ============================================================
# LOGIN BOEING
# ============================================================

def login_boeing(driver):
    log("INFO", "Abrindo Boeing Toolbox")

    driver.get(BOEING_HOME_URL)

    wait = WebDriverWait(
        driver,
        ELEMENT_TIMEOUT,
        poll_frequency=0.5,
    )

    email = wait.until(
        EC.visibility_of_element_located(
            (
                By.CSS_SELECTOR,
                'input[formcontrolname="userReference"]',
            )
        )
    )

    email.clear()
    email.send_keys(BOEING_EMAIL)

    next_boeing = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//button[.//span[normalize-space()='NEXT']]",
            )
        )
    )

    click_element(driver, next_boeing)
    log("OK", "Email Boeing enviado")

    ms_email = wait.until(
        EC.visibility_of_element_located(
            (By.ID, "i0116")
        )
    )

    ms_email.clear()
    ms_email.send_keys(BOEING_EMAIL)

    ms_next = wait.until(
        EC.element_to_be_clickable(
            (By.ID, "idSIButton9")
        )
    )

    click_element(driver, ms_next)
    log("OK", "Email Microsoft enviado")

    password = BOEING_PASSWORD.strip()
    if not password:
        password = getpass("Senha Microsoft: ")

    ms_password = wait.until(
        EC.visibility_of_element_located(
            (By.ID, "i0118")
        )
    )

    ms_password.clear()
    ms_password.send_keys(password)

    ms_signin = wait.until(
        EC.element_to_be_clickable(
            (By.ID, "idSIButton9")
        )
    )

    click_element(driver, ms_signin)
    log("OK", "Senha Microsoft enviada")

    # --------------------------------------------------------
    # MICROSOFT MFA
    # --------------------------------------------------------
    def authy_screen(d):
        try:
            items = d.find_elements(
                By.XPATH,
                (
                    "//span[contains(@class,'mat-radio-label-content') "
                    "and contains(normalize-space(),'Login with Authy App')]"
                ),
            )
            return any(item.is_displayed() for item in items)
        except Exception:
            return False

    log(
        "2FA",
        f"Conclua o MFA Microsoft manualmente. Timeout={MICROSOFT_2FA_TIMEOUT}s",
    )

    WebDriverWait(
        driver,
        MICROSOFT_2FA_TIMEOUT,
        poll_frequency=1,
    ).until(authy_screen)

    log("OK", "Microsoft MFA concluido")

    # --------------------------------------------------------
    # AUTHY
    # --------------------------------------------------------
    authy = wait.until(
        EC.visibility_of_element_located(
            (
                By.XPATH,
                (
                    "//span[contains(@class,'mat-radio-label-content') "
                    "and contains(normalize-space(),'Login with Authy App')]"
                ),
            )
        )
    )

    try:
        label = authy.find_element(
            By.XPATH,
            "./ancestor::label[1]",
        )
        click_element(driver, label)
    except Exception:
        click_element(driver, authy)

    next_authy = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                (
                    "//button[@type='submit' and .//span["
                    "normalize-space()='NEXT']]"
                ),
            )
        )
    )

    click_element(driver, next_authy)
    log("OK", "Authy enviado")

    log(
        "2FA",
        "Aguardando aprovacao/entrada no Boeing Toolbox",
    )

    def toolbox_ready(d):
        try:
            url = d.current_url.lower()
            return "toolbox.boeing.com/webui" in url
        except Exception:
            return False

    WebDriverWait(
        driver,
        TOOLBOX_READY_TIMEOUT,
        poll_frequency=1,
    ).until(toolbox_ready)

    if POST_LOGIN_WAIT > 0:
        time.sleep(POST_LOGIN_WAIT)

    log("OK", "Boeing Toolbox carregado")


# ============================================================
# GOOGLE SHEETS
# ============================================================

def load_sheet():
    if not GOOGLE_CREDENTIALS.exists():
        raise FileNotFoundError(
            f"credentials.json nao encontrado: {GOOGLE_CREDENTIALS}"
        )

    credentials = service_account.Credentials.from_service_account_file(
        str(GOOGLE_CREDENTIALS),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    log(
        "OK",
        f"Google Service Account: {credentials.service_account_email}",
    )

    gc = gspread.authorize(credentials)
    spreadsheet = gc.open_by_url(GOOGLE_SHEET_URL)
    worksheet = spreadsheet.worksheet(GOOGLE_SHEET_TAB)

    rows = worksheet.get_all_values()

    if not rows:
        raise RuntimeError("Aba Boeing vazia.")

    expected = [
        "Part Number",
        "STATUS",
        "DETALHE_STATUS",
        "LINK_PDF",
    ]

    if rows[0][:4] != expected:
        raise RuntimeError(
            f"Cabecalho inesperado na aba {GOOGLE_SHEET_TAB}: {rows[0]}"
        )

    return spreadsheet, worksheet, rows


def prepare_queue(rows):
    queue = []

    for row_number, row in enumerate(rows[1:], start=2):
        pn = row[0].strip() if len(row) >= 1 else ""
        status = row[1].strip() if len(row) >= 2 else ""

        if not pn:
            continue

        status_norm = normalize_text(status)

        if (
            PROCESS_ONLY_BLANK_STATUS
            and status
            and status_norm not in RETRY_STATUSES
        ):
            continue

        queue.append((row_number, pn))

    if MAX_ITEMS > 0:
        queue = queue[:MAX_ITEMS]

    return queue


def update_sheet_row(worksheet, row_number, status, detail, link_pdf=None):
    if not UPDATE_SHEET:
        return

    # Atualiza B:C sem tocar no D por padrao.
    worksheet.update(
        values=[[status, detail]],
        range_name=f"B{row_number}:C{row_number}",
    )

    if UPDATE_LINK_PDF and link_pdf:
        worksheet.update(
            values=[[link_pdf]],
            range_name=f"D{row_number}",
        )


# ============================================================
# URL DE RESULTADOS
# ============================================================

def build_results_url(pn):
    term = f'"{pn}"' if SEARCH_EXACT else pn
    encoded_term = quote(term, safe="")

    # A tela real observada no Toolbox inclui este facet e o backend
    # recebe filterSetList com SDU_INCLUDE_PREV_REVISION=false.
    selected_facets = json.dumps(
        [[
            {
                "fieldName": "SDU_INCLUDE_PREV_REVISION",
                "filterValue": "false",
                "displayFilterValue": "SDU_INCLUDE_PREV_REVISION",
            }
        ]],
        separators=(",", ":"),
    )

    encoded_facets = quote(
        selected_facets,
        safe="",
    )

    return (
        "https://toolbox.boeing.com/webui/results;"
        f"selectedFacets={encoded_facets};"
        f"searchTerm={encoded_term};"
        "searchType=exactMatchOnly;"
        f"lastIndex={SEARCH_SIZE};"
        "sortBy=SPI_DISPLAY_DOC_TYPE;"
        "sortOrder=asc"
    )


# ============================================================
# PERFORMANCE / NETWORK
# ============================================================

def drain_performance_logs(driver):
    try:
        return driver.get_log("performance")
    except Exception:
        return []


def parse_performance_messages(raw_logs):
    messages = []

    for raw in raw_logs:
        try:
            messages.append(
                json.loads(raw["message"])["message"]
            )
        except Exception:
            pass

    return messages


def extract_header_case_insensitive(headers, wanted):
    wanted_lower = wanted.lower()

    for key, value in (headers or {}).items():
        if str(key).lower() == wanted_lower:
            return value

    return None


def find_search_input(driver, timeout=30):
    """
    Localiza o campo de pesquisa do Boeing Toolbox.

    Priorizamos elementos que o proprio Angular identifica como
    campo de busca. Como fallback, escolhemos o maior input de texto
    visivel da barra superior.
    """

    wait = WebDriverWait(
        driver,
        timeout,
        poll_frequency=0.5,
    )

    selectors = [
        'input[placeholder*="Search" i]',
        'input[aria-label*="Search" i]',
        'input[matinput][type="text"]',
        'input.mat-input-element',
        'input[type="text"]',
        'input:not([type])',
    ]

    def locate(d):
        candidates = []

        for selector in selectors:
            try:
                elements = d.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue

            for element in elements:
                try:
                    if not element.is_displayed() or not element.is_enabled():
                        continue

                    rect = d.execute_script(
                        """
                        const r = arguments[0].getBoundingClientRect();
                        return {x:r.x, y:r.y, width:r.width, height:r.height};
                        """,
                        element,
                    )

                    width = float(rect.get("width", 0) or 0)
                    height = float(rect.get("height", 0) or 0)
                    y = float(rect.get("y", 99999) or 99999)

                    if width < 100 or height < 10:
                        continue

                    candidates.append((selector, y, width, element))

                except Exception:
                    continue

            if candidates:
                # Nao precisamos testar seletores de fallback depois
                # que encontramos um seletor especifico.
                break

        if not candidates:
            return False

        # Preferir a barra superior e o maior campo.
        candidates.sort(
            key=lambda item: (
                0 if item[1] < 250 else 1,
                -item[2],
            )
        )

        return candidates[0][3]

    return wait.until(locate)


def trigger_search_from_ui(driver, pn, timeout=60):
    """
    Dispara a pesquisa usando a interface Angular do Boeing Toolbox.

    Isto substitui a antiga estrategia de driver.get(/webui/results...).
    Assim, a propria aplicacao gera a chamada oficial de
    /search/data-unit-contents com X-Auth-Token/X-XSRF-TOKEN.
    """

    # Primeiro tenta usar a barra existente na pagina atual.
    # Isso evita uma navegacao desnecessaria e deixa o Angular
    # controlar a pesquisa. Se nao houver campo, volta para Home.
    try:
        search_input = find_search_input(
            driver,
            timeout=5,
        )
    except Exception:
        driver.get(BOEING_HOME_URL)

        try:
            WebDriverWait(
                driver,
                ELEMENT_TIMEOUT,
                poll_frequency=0.5,
            ).until(
                lambda d: "toolbox.boeing.com/webui" in d.current_url.lower()
            )
        except TimeoutException:
            pass

        search_input = find_search_input(
            driver,
            timeout=ELEMENT_TIMEOUT,
        )

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});",
            search_input,
        )
    except Exception:
        pass

    search_input.click()
    search_input.send_keys(Keys.CONTROL, "a")
    search_input.send_keys(pn)

    # Importante: o ENTER dispara a pesquisa pelo Angular.
    search_input.send_keys(Keys.ENTER)

    # Aguarda o Angular atualizar a rota para /results.
    def results_route(d):
        try:
            url = d.current_url.lower()
            return "/webui/results" in url and "searchterm=" in url
        except Exception:
            return False

    try:
        WebDriverWait(
            driver,
            timeout,
            poll_frequency=0.25,
        ).until(results_route)
    except TimeoutException:
        # A SPA pode manter a mesma rota por alguns instantes.
        # A captura da API continua sendo a fonte de verdade.
        pass

    # Pequena margem para os requests XHR/Fetch entrarem no buffer.
    time.sleep(0.5)

    return driver.current_url


def parse_search_terms(post_data):
    """Extrai o campo terms do JSON enviado pela API de busca."""

    if not post_data:
        return ""

    try:
        payload = json.loads(post_data)
        value = payload.get("terms", "")
        return str(value or "")
    except Exception:
        return str(post_data)


def search_terms_match(post_data, pn):
    """
    Valida que a requisicao capturada realmente pertence ao PN atual.

    Exemplo esperado no Boeing:
        terms = "725177"

    Para PN 0FL1200A06G01, uma requisicao de 725177 e descartada.
    """

    terms = parse_search_terms(post_data)

    expected = f'"{pn}"' if SEARCH_EXACT else pn

    normalized_terms = normalize_text(terms)
    normalized_expected = normalize_text(expected)

    if normalized_terms == normalized_expected:
        return True

    # Fallback: algumas versoes podem serializar/remover aspas.
    return normalize_pn(terms) == normalize_pn(pn)


def capture_search_response(
    driver,
    pn,
    timeout=60,
):
    """
    Executa a busca pela interface real do Boeing Toolbox e captura
    SOMENTE a chamada /search/data-unit-contents cujo payload pertence
    ao PN atual.

    Nao existe POST/fetch manual aqui.
    """

    # Remove qualquer request anterior do buffer.
    drain_performance_logs(driver)

    # Dispara a busca pelo Angular.
    results_url = trigger_search_from_ui(
        driver,
        pn,
        timeout=timeout,
    )

    deadline = time.time() + timeout

    requests_by_id = {}
    wrong_terms_seen = set()
    last_status = None
    last_mime = None
    search_request_seen = False

    while time.time() < deadline:
        raw_logs = drain_performance_logs(driver)
        messages = parse_performance_messages(raw_logs)

        for message in messages:
            method = message.get("method")
            params = message.get("params", {})
            request_id = params.get("requestId")

            if not request_id:
                continue

            if method == "Network.requestWillBeSent":
                request = params.get("request", {})
                url = request.get("url", "")

                if SEARCH_API_PATH not in url:
                    continue

                post_data = request.get("postData", "")

                # Esta e a protecao principal contra capturar a pesquisa
                # anterior (ex.: 725177) durante o processamento de 0FL...
                if not search_terms_match(post_data, pn):
                    terms = parse_search_terms(post_data)
                    wrong_terms_seen.add(terms)
                    continue

                search_request_seen = True

                requests_by_id[request_id] = {
                    "url": url,
                    "method": request.get("method", ""),
                    "headers": request.get("headers", {}),
                    "postData": post_data,
                    "type": params.get("type", ""),
                }

            elif method == "Network.responseReceived":
                response = params.get("response", {})
                url = response.get("url", "")

                if SEARCH_API_PATH not in url:
                    continue

                last_status = response.get("status")
                last_mime = response.get("mimeType", "")

                if request_id in requests_by_id:
                    requests_by_id[request_id]["response"] = {
                        "status": response.get("status"),
                        "mimeType": response.get("mimeType", ""),
                        "url": url,
                    }

        # Tentar somente requests que pertencem ao PN atual.
        for request_id, request_info in list(
            requests_by_id.items()
        ):
            response_info = request_info.get(
                "response",
                {}
            )

            if response_info.get("status") != 200:
                continue

            try:
                body = driver.execute_cdp_cmd(
                    "Network.getResponseBody",
                    {"requestId": request_id},
                )

                body_text = body.get(
                    "body",
                    ""
                )

                if body.get("base64Encoded"):
                    import base64

                    body_text = base64.b64decode(
                        body_text
                    ).decode(
                        "utf-8",
                        errors="replace",
                    )

                data = json.loads(
                    body_text
                )

                results = extract_results(
                    data
                )

                if not isinstance(results, list):
                    continue

                log(
                    "API",
                    (
                        f"{pn}: HTTP 200 "
                        f"| {len(results)} documento(s)"
                    ),
                )

                return (
                    data,
                    request_info.get(
                        "headers",
                        {}
                    ),
                )

            except Exception:
                # O response pode ainda estar sendo finalizado.
                # Tentaremos novamente no proximo ciclo.
                continue

        time.sleep(0.2)

    # Se a API foi vista, mas com erro, informa o erro real.
    if search_request_seen and last_status is not None and last_status != 200:
        raise RuntimeError(
            (
                f"API search retornou HTTP {last_status} "
                f"({last_mime or 'sem MIME'}) "
                f"para o PN {pn}."
            )
        )

    if wrong_terms_seen:
        termos = sorted(
            wrong_terms_seen
        )[:5]

        raise RuntimeError(
            (
                f"A busca foi iniciada para {pn}, mas as respostas "
                f"capturadas pertencem a outros termos: {termos}."
            )
        )

    raise RuntimeError(
        (
            f"Nao foi possivel capturar a resposta JSON correta da API "
            f"{SEARCH_API_PATH} para {pn}."
        )
    )


# ============================================================
# RESULTADOS DA API
# ============================================================

def extract_results(data):
    if isinstance(data, dict):
        value = data.get("results")
        if isinstance(value, list):
            return value

        for key in ["data", "items", "content", "documents"]:
            value = data.get(key)
            if isinstance(value, list):
                return value

        for value in data.values():
            nested = extract_results(value)
            if nested:
                return nested

    elif isinstance(data, list):
        return data

    return []


def normalize_document(result):
    return {
        "raw": result,
        "type": document_type(result),
        "is_pdf": is_pdf_result(result),
        "accessible": is_accessible(result),
        "not_latest": is_not_latest(result),
        "document_number": first_value(
            result,
            [
                "productNumber",
                "searchableProductNumber",
                "displayDocumentIdentifier",
                "documentIdentifier",
                "documentNumber",
            ],
            "",
        ),
        "title": first_value(
            result,
            [
                "displayDocumentTitle",
                "displayDocumentIdentifier",
                "productTitle",
                "originalTitle",
                "title",
            ],
            "",
        ),
        "issue_date": first_value(
            result,
            [
                "issued",
                "issueDate",
                "publicationDate",
                "publishedDate",
            ],
            "",
        ),
        "issue_level": first_value(
            result,
            [
                "issueLevel",
                "issue_level",
                "revision",
                "revisionNumber",
            ],
            "",
        ),
        "revision_state": first_value(
            result,
            [
                "revisionState",
                "revision_state",
                "status",
            ],
            "",
        ),
        "product_instance_id": first_value(
            result,
            [
                "productInstanceId",
                "product_instance_id",
            ],
            "",
        ),
        "data_unit_id": first_value(
            result,
            ["dataUnitId", "data_unit_id"],
            "",
        ),
        "metadata_id": first_value(
            result,
            ["metadataId", "metadata_id"],
            "",
        ),
        "product_id": first_value(
            result,
            ["productId", "product_id"],
            "",
        ),
        "product_format": first_value(
            result,
            ["productFormat", "productFormatLabel"],
            "",
        ),
        "location_key": first_value(
            result,
            ["locationKey", "location"],
            "",
        ),
        "content_object_id": first_value(
            result,
            [
                "contentObjectId",
                "content_object_id",
                "contentObjectID",
            ],
            "",
        ),
    }


def choose_best_document(data):
    raw_documents = extract_results(data)

    documents = [
        normalize_document(item)
        for item in raw_documents
        if isinstance(item, dict)
    ]

    pdf_documents = [
        doc
        for doc in documents
        if doc["is_pdf"]
        and doc["accessible"]
    ]

    if not pdf_documents:
        return None, documents

    # --------------------------------------------------------
    # PRIORIDADE EXPLICITAMENTE PEDIDA:
    # CMM -> GPSD -> IPL -> AMM
    # --------------------------------------------------------
    priority = [
        "CMM",
        "GPSD",
        "IPL",
        "AMM",
    ]

    for wanted_type in priority:
        candidates = [
            doc
            for doc in pdf_documents
            if normalize_text(doc["type"]) == wanted_type
        ]

        if not candidates:
            continue

        # Primeiro damos preferencia ao que nao esta marcado como notLatest.
        current_candidates = [
            doc
            for doc in candidates
            if not doc["not_latest"]
        ]

        if current_candidates:
            candidates = current_candidates

        candidates.sort(
            key=lambda item: (
                parse_date(item["issue_date"]),
                revision_number(item["issue_level"]),
                normalize_text(item["document_number"]),
            ),
            reverse=True,
        )

        return candidates[0], documents

    return None, documents


# ============================================================
# DOCUMENT VIEWER / CONTENT FILE
# ============================================================

def build_document_viewer_url(pn, document):
    document_number = document.get("document_number") or ""
    issue_date = document.get("issue_date") or ""
    product_format = document.get("product_format") or "PDF"
    revision_state = document.get("revision_state") or "PUBLISHED"
    location = document.get("location_key") or document_number

    # Para o viewer observado no Toolbox, issueDate deve ser ISO.
    parsed = parse_date(issue_date)
    if parsed != datetime.min:
        issue_date_iso = parsed.strftime("%Y-%m-%dT00:00:00Z")
    else:
        issue_date_iso = issue_date

    search_term = quote(
        f'"{pn}"' if SEARCH_EXACT else pn,
        safe="",
    )

    # Matrix params, conforme a rota observada:
    # /webui/DocumentViewer;productNumber=...;issueDate=...;...
    parts = [
        "https://toolbox.boeing.com/webui/DocumentViewer",
        f"productNumber={quote(str(document_number), safe='')}",
        f"issueDate={quote(str(issue_date_iso), safe='')}",
        f"productFormat={quote(str(product_format), safe='')}",
        f"revisionState={quote(str(revision_state or 'PUBLISHED'), safe='')}",
        f"location={quote(str(location), safe='')}",
        f"searchTerm={search_term}",
    ]

    return ";".join(parts)


def enable_network_capture(driver):
    """Habilita captura de rede no alvo/janela atual."""
    try:
        driver.execute_cdp_cmd(
            "Network.enable",
            {
                "maxTotalBufferSize": 100000000,
                "maxResourceBufferSize": 50000000,
                "maxPostDataSize": 10000000,
            },
        )
    except Exception:
        pass


def find_resource_urls_from_page(driver):
    """Retorna URLs carregadas pelo alvo atual via Resource Timing."""
    try:
        result = driver.execute_script(
            "return performance.getEntriesByType('resource').map(e => e.name);"
        )
        if isinstance(result, list):
            return [str(item) for item in result if item]
    except Exception:
        pass
    return []


def extract_content_object_id_from_url(url):
    """Extrai contentObjectId de rotas contentFile/contentByProduct..."""
    if not url:
        return None

    match = re.search(
        r"/product/fileRepo/contentFile/([^/?#]+)",
        url,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)

    match = re.search(
        r"/productv2/fileRepo/contentByProductInstanceIdAndContentObjectId/[^/]+/([^/?#]+)",
        url,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)

    return None


def find_content_file_request(
    driver,
    timeout=45,
):
    """
    Procura a requisicao real do PDF no alvo atual.

    Alem do endpoint contentFile, considera a rota productv2 usada pelo
    viewer. Se encontrar o contentObjectId por qualquer uma dessas rotas,
    devolve-o para que o caller possa montar o contentFile diretamente.
    """
    deadline = time.time() + timeout
    requests_by_id = {}

    enable_network_capture(driver)

    while time.time() < deadline:
        # 1) Eventos CDP/performance
        raw_logs = drain_performance_logs(driver)
        messages = parse_performance_messages(raw_logs)

        for message in messages:
            method = message.get("method")
            params = message.get("params", {})
            request_id = params.get("requestId")

            if not request_id:
                continue

            if method == "Network.requestWillBeSent":
                request = params.get("request", {})
                url = request.get("url", "")

                if (
                    "/product/fileRepo/contentFile/" in url
                    or
                    "/productv2/fileRepo/contentByProductInstanceIdAndContentObjectId/" in url
                ):
                    content_object_id = extract_content_object_id_from_url(url)
                    return {
                        "url": url,
                        "headers": request.get("headers", {}),
                        "method": request.get("method", "GET"),
                        "contentObjectId": content_object_id,
                    }

                requests_by_id[request_id] = {
                    "url": url,
                    "headers": request.get("headers", {}),
                    "method": request.get("method", ""),
                }

            elif method == "Network.responseReceived":
                response = params.get("response", {})
                url = response.get("url", "")

                if (
                    "/product/fileRepo/contentFile/" in url
                    or
                    "/productv2/fileRepo/contentByProductInstanceIdAndContentObjectId/" in url
                ):
                    item = requests_by_id.get(request_id, {})
                    content_object_id = extract_content_object_id_from_url(url)
                    return {
                        "url": url,
                        "headers": item.get("headers", {}),
                        "method": item.get("method", "GET"),
                        "status": response.get("status"),
                        "mimeType": response.get("mimeType", ""),
                        "contentObjectId": content_object_id,
                    }

        # 2) Resource Timing da pagina atual. Isso eh especialmente importante
        # quando o PDF abriu em uma nova aba/alvo do Chrome.
        for url in find_resource_urls_from_page(driver):
            content_object_id = extract_content_object_id_from_url(url)
            if content_object_id:
                return {
                    "url": url,
                    "headers": {},
                    "method": "GET",
                    "contentObjectId": content_object_id,
                }

        time.sleep(0.25)

    return None



def find_pdf_link_for_document(driver, document):
    """
    Procura, na tela de resultados atual, o link/botao PDF pertencente
    ao documento selecionado.

    O probe que funcionou no ambiente real mostrou que o clique no PDF
    e que dispara a abertura da pagina/aba do viewer e a chamada
    /product/fileRepo/contentFile/. Por isso, preferimos reproduzir
    exatamente esse comportamento em vez de montar manualmente a URL
    do DocumentViewer.
    """

    document_number = normalize_text(
        document.get("document_number") or ""
    )

    if not document_number:
        return None

    # --------------------------------------------------------
    # Todos os elementos visiveis que parecem ser PDF.
    # --------------------------------------------------------
    candidatos = driver.find_elements(
        By.XPATH,
        (
            "//*[self::a or self::button or @role='button' or self::span]"
            "[contains(translate(normalize-space(.), 'pdf', 'PDF'), 'PDF')]"
        )
    )

    # --------------------------------------------------------
    # Para cada candidato, sobe alguns niveis e verifica se o
    # container tambem contem o numero do documento selecionado.
    # --------------------------------------------------------
    for candidato in candidatos:
        try:
            if not candidato.is_displayed():
                continue

            for level in range(1, 8):
                try:
                    container = candidato.find_element(
                        By.XPATH,
                        "./ancestor::*[%d]" % level,
                    )
                except Exception:
                    continue

                texto = normalize_text(
                    container.text
                )

                if document_number in texto:
                    return candidato

        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    return None


def discover_content_file_by_click(
    driver,
    pn,
    document,
    timeout=90,
):
    """
    Metodo principal para descobrir o endpoint contentFile.

    1. Continua na pagina /results.
    2. Localiza o PDF do documento escolhido.
    3. Clica no PDF como no uso manual.
    4. Detecta nova aba/janela.
    5. Captura a requisicao real /product/fileRepo/contentFile/.

    Retorna (content_url, request_headers) ou (None, {}).
    """

    pdf_element = find_pdf_link_for_document(
        driver,
        document,
    )

    if pdf_element is None:
        return None, {}

    handles_before = list(
        driver.window_handles
    )

    # Limpa requests antigos para evitar capturar outro contentFile.
    drain_performance_logs(driver)

    log(
        "DOC",
        (
            f"Abrindo PDF pelo resultado | "
            f"{document['type']} | "
            f"{document['document_number']}"
        ),
    )

    try:
        click_element(
            driver,
            pdf_element,
        )
    except Exception:
        return None, {}

    # --------------------------------------------------------
    # Aguarda nova aba ou navegacao.
    # --------------------------------------------------------
    try:
        WebDriverWait(
            driver,
            30,
            poll_frequency=0.25,
        ).until(
            lambda d: (
                len(d.window_handles) > len(handles_before)
                or
                any(
                    "/documentviewer" in str(d.current_url).lower()
                    for _ in [0]
                )
            )
        )
    except TimeoutException:
        pass

    # Se uma nova aba foi criada, usa-la.
    handles_after = list(
        driver.window_handles
    )

    novas = [
        handle
        for handle in handles_after
        if handle not in handles_before
    ]

    if novas:
        try:
            driver.switch_to.window(
                novas[-1]
            )
            # Cada nova aba eh um alvo diferente; habilitamos a captura
            # tambem nesse alvo antes de provocar uma nova carga.
            enable_network_capture(driver)
        except Exception:
            pass

    # --------------------------------------------------------
    # Aguarda a pagina do viewer iniciar e tenta capturar a chamada.
    # --------------------------------------------------------
    time.sleep(1.0)

    request = find_content_file_request(
        driver,
        timeout=min(timeout, 30),
    )

    # Se o evento aconteceu antes do attach do Network ao novo alvo,
    # um reload do viewer normalmente faz o Boeing repetir a requisicao.
    if request is None:
        try:
            current_url = driver.current_url
            if current_url and "/documentviewer" in current_url.lower():
                drain_performance_logs(driver)
                enable_network_capture(driver)
                try:
                    driver.refresh()
                except Exception:
                    pass
                time.sleep(1.0)
                request = find_content_file_request(
                    driver,
                    timeout=min(timeout, 45),
                )
        except Exception:
            pass

    if request:
        content_url = request.get("url")
        content_object_id = request.get("contentObjectId")

        # Se o viewer informou a rota productv2, convertemos para o endpoint
        # direto contentFile, que foi validado na captura manual.
        if (
            content_url
            and "/productv2/fileRepo/contentByProductInstanceIdAndContentObjectId/" in content_url
            and content_object_id
        ):
            content_url = urljoin(
                "https://toolbox.boeing.com",
                CONTENT_FILE_PATH.format(
                    content_object_id=content_object_id
                ),
            )

        if content_url:
            log(
                "API",
                "Endpoint contentFile localizado pelo clique no PDF",
            )

            return (
                content_url,
                request.get(
                    "headers",
                    {}
                ),
            )

    return None, {}


def discover_content_file_url(
    driver,
    pn,
    document,
):
    """
    Descobre o endpoint real do PDF.

    PRINCIPAL:
        clica no PDF diretamente na lista de resultados.

    FALLBACK:
        abre o DocumentViewer construido a partir dos metadados.
    """

    # ========================================================
    # METODO PRINCIPAL - CLIQUE REAL NO PDF
    # ========================================================

    content_url, headers = discover_content_file_by_click(
        driver,
        pn,
        document,
        timeout=90,
    )

    if content_url:
        return content_url, headers

    # ========================================================
    # FALLBACK - VIEWER CONSTRUIDO
    # ========================================================

    drain_performance_logs(driver)

    viewer_url = build_document_viewer_url(
        pn,
        document,
    )

    log(
        "DOC",
        (
            f"Fallback viewer | "
            f"{document['type']} | "
            f"{document['document_number']}"
        ),
    )

    handles_before = list(
        driver.window_handles
    )

    try:
        driver.get(viewer_url)
    except Exception:
        # Com page_load_strategy=none podemos continuar mesmo se o
        # renderer interromper o comando durante o carregamento.
        pass

    request = find_content_file_request(
        driver,
        timeout=60,
    )

    if request:
        content_url = request.get("url")
        content_object_id = request.get("contentObjectId")

        if (
            content_url
            and "/productv2/fileRepo/contentByProductInstanceIdAndContentObjectId/" in content_url
            and content_object_id
        ):
            content_url = urljoin(
                "https://toolbox.boeing.com",
                CONTENT_FILE_PATH.format(
                    content_object_id=content_object_id
                ),
            )

        if content_url:
            log(
                "API",
                "Endpoint contentFile localizado no fallback viewer",
            )

            return (
                content_url,
                request.get(
                    "headers",
                    {}
                ),
            )

    # ========================================================
    # FALLBACK EXTRA - procura qualquer nova janela gerada.
    # ========================================================

    handles_after = list(
        driver.window_handles
    )

    novas = [
        handle
        for handle in handles_after
        if handle not in handles_before
    ]

    for handle in novas:
        try:
            driver.switch_to.window(
                handle
            )

            request = find_content_file_request(
                driver,
                timeout=15,
            )

            if request and request.get("url"):
                return (
                    request["url"],
                    request.get("headers", {}),
                )

        except Exception:
            continue

    return None, {}


# ============================================================
# SESSION HTTP AUTENTICADA
# ============================================================

def build_authenticated_session(
    driver,
    captured_headers=None,
):
    session = requests.Session()

    try:
        user_agent = driver.execute_script(
            "return navigator.userAgent;"
        )
    except Exception:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        )

    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
        }
    )

    # --------------------------------------------------------
    # Copia cookies da sessao Selenium.
    # --------------------------------------------------------
    for cookie in driver.get_cookies():
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )

    # --------------------------------------------------------
    # Copia tokens capturados do request oficial.
    # --------------------------------------------------------
    if captured_headers:
        for source_name in [
            "X-Auth-Token",
            "X-XSRF-TOKEN",
            "x-auth-token",
            "x-xsrf-token",
        ]:
            value = extract_header_case_insensitive(
                captured_headers,
                source_name,
            )

            if value:
                if source_name.lower() == "x-auth-token":
                    session.headers["X-Auth-Token"] = value
                elif source_name.lower() == "x-xsrf-token":
                    session.headers["X-XSRF-TOKEN"] = value

    return session


# ============================================================
# DOWNLOAD PDF VIA API
# ============================================================

def download_pdf(
    driver,
    content_url,
    content_headers,
    selected,
    pn,
):
    session = build_authenticated_session(
        driver,
        captured_headers=content_headers,
    )

    # O contentFile nao precisa da navegacao do viewer; fazemos GET direto.
    headers = {
        "Referer": driver.current_url,
        "Accept": "application/pdf,*/*",
        "X-Requested-With": "XMLHttpRequest",
    }

    x_auth = extract_header_case_insensitive(
        content_headers,
        "X-Auth-Token",
    )

    if x_auth:
        headers["X-Auth-Token"] = x_auth

    response = session.get(
        content_url,
        headers=headers,
        timeout=DOWNLOAD_TIMEOUT,
        verify=False,
        allow_redirects=True,
        stream=True,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"contentFile retornou HTTP {response.status_code}"
        )

    content_type = (
        response.headers.get("Content-Type", "")
        .lower()
    )

    if "text/html" in content_type:
        raise RuntimeError(
            "contentFile retornou HTML em vez de PDF."
        )

    # --------------------------------------------------------
    # Nome original do servidor
    # --------------------------------------------------------
    content_disposition = response.headers.get(
        "Content-Disposition",
        "",
    )

    original_name = ""

    match = re.search(
        r'filename="?([^";]+)"?',
        content_disposition,
        flags=re.IGNORECASE,
    )

    if match:
        original_name = match.group(1).strip()

    # --------------------------------------------------------
    # Nome local padronizado
    # --------------------------------------------------------
    doc_type = safe_filename(
        selected.get("type") or "DOC"
    )

    doc_number = safe_filename(
        selected.get("document_number") or pn
    )

    rev = safe_filename(
        selected.get("issue_level") or ""
    )

    issue_date = selected.get("issue_date") or ""

    if isinstance(issue_date, str):
        parsed = parse_date(issue_date)
        if parsed != datetime.min:
            issue_date = parsed.strftime("%Y-%m-%d")

    issue_date = safe_filename(
        issue_date
    )

    filename_parts = [
        safe_filename(pn),
        doc_type,
        doc_number,
    ]

    if rev:
        filename_parts.append(
            f"Rev_{rev}"
        )

    if issue_date:
        filename_parts.append(
            issue_date
        )

    filename = "__".join(filename_parts)

    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    target_path = DOWNLOAD_DIR / filename

    # --------------------------------------------------------
    # Download em stream para nao carregar 40-50 MB na RAM.
    # --------------------------------------------------------
    first_chunk = None

    with open(target_path, "wb") as output:
        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):
            if not chunk:
                continue

            if first_chunk is None:
                first_chunk = chunk

            output.write(chunk)

    if not target_path.exists():
        raise RuntimeError("Arquivo nao foi criado.")

    size = target_path.stat().st_size

    if size == 0:
        target_path.unlink(missing_ok=True)
        raise RuntimeError("Arquivo PDF vazio.")

    # Validacao simples da assinatura PDF.
    with open(target_path, "rb") as check_file:
        signature = check_file.read(5)

    if signature != b"%PDF-":
        target_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Conteudo baixado nao possui assinatura PDF valida."
        )

    return target_path, original_name, size


# ============================================================
# PROCESSAR PN
# ============================================================

def process_pn(driver, pn):
    data, search_headers = capture_search_response(
        driver,
        pn,
    )

    selected, documents = choose_best_document(
        data
    )

    pdf_documents = [
        doc
        for doc in documents
        if doc["is_pdf"]
    ]

    if not documents:
        return {
            "status": "SEM PDF",
            "detail": "A API nao retornou documentos.",
            "file": None,
        }

    if selected is None:
        types = sorted(
            unique_preserve(
                [
                    normalize_text(doc["type"])
                    for doc in pdf_documents
                    if doc["type"]
                ]
            )
        )

        if types:
            detail = (
                "Existem PDFs, mas nenhum CMM/GPSD/IPL/AMM "
                "elegivel foi localizado. "
                f"Tipos encontrados: {', '.join(types)}"
            )
        else:
            detail = (
                "A API retornou documentos, mas nenhum PDF valido foi localizado."
            )

        return {
            "status": "SEM PDF",
            "detail": detail,
            "file": None,
        }

    # --------------------------------------------------------
    # Garante que continuamos com a credencial mais recente.
    # A busca real ja forneceu X-Auth-Token.
    # --------------------------------------------------------
    if not search_headers.get("X-Auth-Token"):
        log(
            "WARN",
            f"{pn}: X-Auth-Token nao apareceu na busca; usando sessao do navegador.",
        )

    # Se o resultado ja trouxer contentObjectId, usamos diretamente
    # o endpoint contentFile. Caso contrario, o viewer do proprio
    # Boeing gera a chamada contentFile como fallback.
    content_headers = search_headers
    content_url = None

    content_object_id = (
        selected.get("content_object_id")
        or
        first_value(
            selected.get("raw", {}),
            [
                "contentObjectId",
                "content_object_id",
                "contentObjectID",
                "contentId",
                "content_id",
            ],
            "",
        )
    )

    if content_object_id:
        content_url = urljoin(
            "https://toolbox.boeing.com",
            CONTENT_FILE_PATH.format(
                content_object_id=content_object_id
            ),
        )

    if not content_url:
        content_url, content_headers = discover_content_file_url(
            driver,
            pn,
            selected,
        )


    if not content_url:
        return {
            "status": "ERRO",
            "detail": (
                "Documento encontrado, mas nao foi possivel localizar "
                "o endpoint contentFile."
            ),
            "file": None,
        }

    try:
        target_path, original_name, size = download_pdf(
            driver,
            content_url,
            content_headers or search_headers,
            selected,
            pn,
        )

    except Exception as exc:
        return {
            "status": "ERRO",
            "detail": (
                f"Falha no download do {selected['type']}: {exc}"
            ),
            "file": None,
        }

    detail_parts = [
        selected["type"],
        selected["document_number"] or "sem numero",
        f"Rev {selected['issue_level'] or '-'}",
        f"Publicado {selected['issue_date'] or '-'}",
        f"{size / (1024 * 1024):.1f} MB",
    ]

    if original_name:
        detail_parts.append(
            f"Original: {original_name}"
        )

    return {
        "status": "PDF BAIXADO",
        "detail": " | ".join(detail_parts),
        "file": target_path,
    }


# ============================================================
# MAIN
# ============================================================

def run():
    log("INFO", "Iniciando VERIFICAR REF TECNICA - Boeing")

    # --------------------------------------------------------
    # GOOGLE
    # --------------------------------------------------------
    spreadsheet, worksheet, rows = load_sheet()
    queue = prepare_queue(rows)

    total_pns = sum(
        1
        for row in rows[1:]
        if row and row[0].strip()
    )

    log(
        "OK",
        f"Google Sheets conectado | aba={GOOGLE_SHEET_TAB} | PNs={total_pns}",
    )

    if not queue:
        log("OK", "Nenhum PN pendente para processamento.")
        return 0

    log(
        "OK",
        f"Fila preparada | {len(queue)} PN(s)",
    )

    # --------------------------------------------------------
    # CHROME / LOGIN
    # --------------------------------------------------------
    driver = create_driver()
    log("OK", "Chrome iniciado")

    try:
        login_boeing(driver)

        total = len(queue)
        pdfs = 0
        sem_pdf = 0
        erros = 0

        for index, (row_number, pn) in enumerate(
            queue,
            start=1,
        ):
            log(
                "INFO",
                f"[{index}/{total}] PN {pn}",
            )

            try:
                result = process_pn(
                    driver,
                    pn,
                )

            except Exception as exc:
                log_exception(f"Erro processando PN {pn}", exc)

                result = {
                    "status": "ERRO",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "file": None,
                }

            status = result["status"]
            detail = result["detail"]

            try:
                update_sheet_row(
                    worksheet,
                    row_number,
                    status,
                    detail,
                )

            except Exception as exc:
                log_exception(f"Erro atualizando Sheets | PN={pn}", exc)
                erros += 1
                continue

            if status == "PDF BAIXADO":
                pdfs += 1

                file_name = (
                    result["file"].name
                    if result.get("file")
                    else "-"
                )

                log(
                    "OK",
                    f"{pn} | {detail} | {file_name}",
                )

            elif status == "SEM PDF":
                sem_pdf += 1
                log(
                    "SEM PDF",
                    f"{pn} | {detail}",
                )

            else:
                erros += 1
                log(
                    "ERRO",
                    f"{pn} | {detail}",
                )

            if DELAY_BETWEEN_ITEMS > 0:
                time.sleep(DELAY_BETWEEN_ITEMS)

        # ----------------------------------------------------
        # RESUMO
        # ----------------------------------------------------
        print()
        print("-" * 65)
        print("RESUMO")
        print("-" * 65)
        print(f"Processados............... {total}")
        print(f"PDFs baixados............ {pdfs}")
        print(f"Sem PDF.................. {sem_pdf}")
        print(f"Erros.................... {erros}")
        print("-" * 65)
        print(f"Pasta: {DOWNLOAD_DIR}")
        log("OK", "Processamento concluido")
        return 0

    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except KeyboardInterrupt:
        log("WARN", "Execucao interrompida pelo usuario.")
        raise SystemExit(1)
    except Exception as exc:
        log_exception("Falha fatal", exc)
        raise SystemExit(1)
