# -*- coding: utf-8 -*-
LOGIC_LAST_MODIFIED = "2026-10-27"  # ⭐ 실시간 핑 측정 및 자동 예측 샷 적용

import os
import re
import time
from datetime import datetime, timedelta, timezone
from getpass import getpass
from typing import Optional, Tuple, List, Dict
import threading
import signal
import platform
import requests
import sys
import atexit
import uuid
import urllib.parse
import urllib3

# SSL 경고 숨김 (핑 체크용)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options


# =========================
# 사이트/실행 설정
# =========================
SITE_CONFIGS = {
    "gt": {
        "site_name": "gt",
        "login_url": "https://gtog.shopreview.co.kr/usr/login_form",
        "list_url": "https://gtog.shopreview.co.kr/usr",
        "allowed_deposit_modes": {
            "ETC": {"VENDOR"},
            "REAL": {"VENDOR", "GOODTO"},
        },
        "persist_account_fail": True,
        "retry_second_account": True,
        "success_csq_file": "success_csq_gt.txt",
        "account_fail_csq_file": "account_fail_csq_gt.txt",
    },
    "dbd": {
        "site_name": "dbd",
        "login_url": "https://dbg.shopreview.co.kr/usr/login_form",
        "list_url": "https://dbg.shopreview.co.kr/usr",
        "allowed_deposit_modes": None,
        "persist_account_fail": False,
        "retry_second_account": True,
        "success_csq_file": "success_csq_dbd.txt",
        "account_fail_csq_file": None,
    },
}

MIN_PRICE_ETC  = 70000
MIN_PRICE_REAL = 19700

BASE_REFRESH_INTERVAL = 2          
HOT_WINDOW_SEC        = 100        

DETAIL_POLL_SEC        = 0.05      
DETAIL_HTML_POLL_SEC   = 0.15      
DETAIL_MAX_WAIT_SEC    = 75        

DETAIL_POLL_SEC_FAR    = 10.0      
DETAIL_POLL_SEC_MID    = 0.3       
DETAIL_POLL_SEC_NEAR   = DETAIL_POLL_SEC  
DETAIL_POLL_NEAR_THRESHOLD_SEC = 10
DETAIL_POLL_MID_THRESHOLD_SEC  = 60


def get_adaptive_poll_sec(sec_to_open: Optional[float]) -> float:
    if sec_to_open is None:
        return DETAIL_POLL_SEC_NEAR
    if sec_to_open > DETAIL_POLL_MID_THRESHOLD_SEC:
        return DETAIL_POLL_SEC_FAR
    if sec_to_open > DETAIL_POLL_NEAR_THRESHOLD_SEC:
        return DETAIL_POLL_SEC_MID
    return DETAIL_POLL_SEC_NEAR

REQUEST_TIMEOUT_SEC    = 2.5
CONGESTION_RETRY_MAX       = 5     
CONGESTION_RETRY_DELAY_SEC = 0.3   

MAX_SCROLL = 10
HEADLESS = True
LOG_SAVE = False   

TARGET_CSQ = ""

# =========================
# 실시간 핑(Ping) 모니터링 시스템
# =========================
gui_ping_callback = None
CURRENT_PING_MS = 0.0
_ping_thread = None
_ping_running = False

def _ping_worker():
    global CURRENT_PING_MS
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0.0.0",
        "Cache-Control": "no-cache"
    })
    while _ping_running:
        try:
            t0 = time.perf_counter()
            s.head(LIST_URL, timeout=2.5, verify=False)
            rtt = (time.perf_counter() - t0) * 1000.0
            
            # EWMA(지수 가중 이동 평균)로 핑 값이 확 튀는 것을 방지하고 부드럽게 갱신
            if CURRENT_PING_MS == 0.0:
                CURRENT_PING_MS = rtt
            else:
                CURRENT_PING_MS = CURRENT_PING_MS * 0.7 + rtt * 0.3
                
            if gui_ping_callback:
                gui_ping_callback(CURRENT_PING_MS)
        except Exception:
            pass
        
        # 2.5초마다 핑 측정 (반응성을 위해 0.1초씩 sleep)
        for _ in range(25):
            if not _ping_running:
                break
            time.sleep(0.1)

def start_ping_monitor():
    global _ping_thread, _ping_running
    if _ping_thread is None or not _ping_thread.is_alive():
        _ping_running = True
        _ping_thread = threading.Thread(target=_ping_worker, daemon=True)
        _ping_thread.start()

def stop_ping_monitor():
    global _ping_running
    _ping_running = False


# =========================
# 저부하 옵션
# =========================
BLOCK_IMAGES = True
BLOCK_FONTS = True
BLOCK_MEDIA = True
USE_REFRESH_OVER_GET = True
LIST_REOPEN_EVERY = 30
POPUP_COOLDOWN_SEC = 3.0

ACTIVE_SITE_KEY = "gt"
SITE_NAME = "gt"
LOGIN_URL = SITE_CONFIGS[ACTIVE_SITE_KEY]["login_url"]
LIST_URL = SITE_CONFIGS[ACTIVE_SITE_KEY]["list_url"]
ALLOWED_DEPOSIT_MODES = SITE_CONFIGS[ACTIVE_SITE_KEY]["allowed_deposit_modes"]
PERSIST_ACCOUNT_FAIL = SITE_CONFIGS[ACTIVE_SITE_KEY]["persist_account_fail"]
RETRY_SECOND_ACCOUNT = SITE_CONFIGS[ACTIVE_SITE_KEY]["retry_second_account"]
SUCCESS_CSQ_FILE = SITE_CONFIGS[ACTIVE_SITE_KEY]["success_csq_file"]
ACCOUNT_FAIL_CSQ_FILE = SITE_CONFIGS[ACTIVE_SITE_KEY]["account_fail_csq_file"]


def apply_site_config(site_key: str):
    global ACTIVE_SITE_KEY, SITE_NAME, LOGIN_URL, LIST_URL
    global ALLOWED_DEPOSIT_MODES, PERSIST_ACCOUNT_FAIL, RETRY_SECOND_ACCOUNT
    global SUCCESS_CSQ_FILE, ACCOUNT_FAIL_CSQ_FILE

    site_key = (site_key or "gt").strip().lower()
    if site_key not in SITE_CONFIGS:
        raise ValueError(f"지원하지 않는 사이트입니다: {site_key} (가능: {', '.join(SITE_CONFIGS)})")

    cfg = SITE_CONFIGS[site_key]
    ACTIVE_SITE_KEY = site_key
    SITE_NAME = cfg["site_name"]
    LOGIN_URL = cfg["login_url"]
    LIST_URL = cfg["list_url"]
    ALLOWED_DEPOSIT_MODES = cfg["allowed_deposit_modes"]
    PERSIST_ACCOUNT_FAIL = cfg["persist_account_fail"]
    RETRY_SECOND_ACCOUNT = cfg["retry_second_account"]
    SUCCESS_CSQ_FILE = cfg["success_csq_file"]
    ACCOUNT_FAIL_CSQ_FILE = cfg["account_fail_csq_file"]


def choose_site() -> str:
    env_site = os.environ.get("SHOP_SITE")
    arg_site = sys.argv[1] if len(sys.argv) > 1 else ""
    picked = (env_site or arg_site).strip().lower()
    if picked in SITE_CONFIGS:
        return picked

    prompt = f"사이트 선택 ({'/'.join(SITE_CONFIGS.keys())}, 기본 gt): "
    picked = input(prompt).strip().lower() or "gt"
    if picked not in SITE_CONFIGS:
        raise ValueError(f"지원하지 않는 사이트입니다: {picked}")
    return picked


# =========================
# 로그 유틸
# =========================
def ts_ms() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def site_log_tag() -> str:
    return "GT" if ACTIVE_SITE_KEY == "gt" else "DB"

LOOP_LOG_DIR = "loop_logs"
CURRENT_LOOP_LOG_FILE: Optional[str] = None
CURRENT_LOOP_DIR: Optional[str] = None
CURRENT_HTML_SEQ = 0

def ensure_loop_log_dir():
    if not LOG_SAVE: return
    os.makedirs(LOOP_LOG_DIR, exist_ok=True)

def _safe_fs_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", str(name or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned or "unnamed"

def begin_loop_log(loop_no: int) -> Optional[str]:
    global CURRENT_LOOP_LOG_FILE, CURRENT_LOOP_DIR, CURRENT_HTML_SEQ

    if not LOG_SAVE:
        CURRENT_LOOP_LOG_FILE = None
        CURRENT_LOOP_DIR = None
        CURRENT_HTML_SEQ = 0
        return None

    ensure_loop_log_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    CURRENT_LOOP_DIR = os.path.join(LOOP_LOG_DIR, f"{SITE_NAME}_loop_{loop_no:06d}_{stamp}")
    os.makedirs(CURRENT_LOOP_DIR, exist_ok=True)
    CURRENT_LOOP_LOG_FILE = os.path.join(CURRENT_LOOP_DIR, "log.txt")
    CURRENT_HTML_SEQ = 0

    with open(CURRENT_LOOP_LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"site={SITE_NAME}\n")
        f.write(f"loop_no={loop_no}\n")
        f.write(f"started_at={datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
        f.write("=" * 80 + "\n")
    return CURRENT_LOOP_LOG_FILE

def end_loop_log(status: str = "done"):
    global CURRENT_LOOP_LOG_FILE, CURRENT_LOOP_DIR, CURRENT_HTML_SEQ
    if not LOG_SAVE or not CURRENT_LOOP_LOG_FILE: return
    try:
        with open(CURRENT_LOOP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f"ended_at={datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
            f.write(f"status={status}\n")
    except Exception: pass
    finally:
        CURRENT_LOOP_LOG_FILE = None
        CURRENT_LOOP_DIR = None
        CURRENT_HTML_SEQ = 0

def save_html(label: str, html: str) -> Optional[str]:
    global CURRENT_HTML_SEQ
    if not LOG_SAVE or not CURRENT_LOOP_DIR or not html: return None
    try:
        CURRENT_HTML_SEQ += 1
        filename = f"{CURRENT_HTML_SEQ:03d}_{_safe_fs_name(label)}.html"
        path = os.path.join(CURRENT_LOOP_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path
    except Exception as e:
        print(f"[{ts_ms()}]{site_log_tag()} / [경고] HTML 저장 실패: {e}")
        return None

def save_current_page_html(driver: webdriver.Chrome, label: str) -> Optional[str]:
    if not LOG_SAVE: return None
    try: return save_html(label, driver.page_source or "")
    except Exception: return None

def _append_loop_log_line(line: str):
    if not LOG_SAVE or not CURRENT_LOOP_LOG_FILE: return
    try:
        with open(CURRENT_LOOP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception: pass

def log(msg: str):
    line = f"[{ts_ms()}]{site_log_tag()} / {msg}"
    print(line)
    _append_loop_log_line(line)

atexit.register(end_loop_log, "process_exit")

def ms_from(t0: float) -> int:
    return int((time.time() - t0) * 1000)

def short_card_desc(csq: str, ctype: str, price: int, secs_to_open=None) -> str:
    if secs_to_open is None:
        return f"csq={csq} type={ctype} price={price:,}"
    return f"csq={csq} type={ctype} price={price:,} open_in={int(secs_to_open)}s"


# =========================
# 일시정지(CTRL+Z) / 재개 토글
# =========================
PAUSED = threading.Event()       
STOP_SIGNAL = threading.Event()  

class StopRequested(Exception): pass

def _toggle_pause():
    if PAUSED.is_set():
        PAUSED.clear()
        log("[진행] 재개")
    else:
        PAUSED.set()
        log("[일시정지] 일시정지 (Ctrl+Z 다시 누르면 재개)")

def pause_point():
    if STOP_SIGNAL.is_set(): raise StopRequested("종료 요청")
    while PAUSED.is_set():
        if STOP_SIGNAL.is_set(): raise StopRequested("종료 요청")
        time.sleep(0.20)

def setup_pause_handlers():
    try:
        if hasattr(signal, "SIGTSTP"):
            signal.signal(signal.SIGTSTP, lambda s, f: _toggle_pause())
    except Exception: pass

    if platform.system().lower().startswith("win"):
        try:
            import msvcrt
            def _win_listener():
                while True:
                    try: ch = msvcrt.getwch()
                    except Exception: break
                    if ch == "\x1a": _toggle_pause()
                    elif ch.lower() == "c":
                        log("[일시정지] 'c' 키 종료 요청")
                        os._exit(0)
            th = threading.Thread(target=_win_listener, daemon=True)
            th.start()
        except Exception: pass


# =========================
# 일반 유틸
# =========================
def safe_text(el) -> str:
    try: return el.text or ""
    except Exception: return ""

def extract_price(text: str) -> int:
    m = re.search(r"([\d,]+)\s*원", text)
    return int(m.group(1).replace(",", "")) if m else 0

def parse_amount_attr(value: str) -> int:
    raw = str(value or "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else 0

def resolve_card_price(amount_attr: str, text: str) -> int:
    amount_price = parse_amount_attr(amount_attr)
    if amount_price > 0: return amount_price
    return extract_price(text)

def detect_type(text: str, html: str = "") -> str:
    blob = ((text or "") + " " + (html or "")).strip()
    if "실배송" in blob: return "REAL"
    if "기타배송" in blob: return "ETC"
    return "UNKNOWN"

def detect_shop(text: str, html: str = "") -> str:
    blob = ((text or "") + " " + (html or "")).strip()
    if "img/shop/naver.png" in blob: return "NAVER"
    return "OTHER"

def detect_deposit_mode(text: str, html: str = "") -> str:
    blob = ((text or "") + " " + (html or "")).strip()
    normalized = re.sub(r"\s+", "", blob).upper()
    if ("트루펄스" in normalized) or ("TRUEPULSE" in normalized): return "TRUEPULSE"
    if ("업체입금" in blob) or ("type_box_deposit_n" in blob): return "VENDOR"
    if ("굿투입금" in blob) or ("type_box_deposit_y" in blob): return "GOODTO"
    return "UNKNOWN"

def is_allowed_deposit_mode(site_key: str, campaign_type: str, deposit_mode: str) -> bool:
    allowed_by_type = SITE_CONFIGS.get(site_key, {}).get("allowed_deposit_modes")
    if allowed_by_type in (None, False): return True
    return deposit_mode in allowed_by_type.get(campaign_type, set())

def load_csq_file(path: str) -> set:
    if not os.path.exists(path): return set()
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text: return set()
    return set(x.strip() for x in text.split(",") if x.strip())

def append_csq_file(path: str, csq: str, label: str):
    csq = str(csq).strip()
    if not csq: return
    saved = load_csq_file(path)
    if csq in saved: return
    mode = "a" if (os.path.exists(path) and os.path.getsize(path) > 0) else "w"
    with open(path, mode, encoding="utf-8") as f:
        f.write(("," if mode == "a" else "") + csq)
    log(f"[완료] {label}: {csq}")

def load_success_csq() -> set: return load_csq_file(SUCCESS_CSQ_FILE)
def save_success_csq(csq: str): append_csq_file(SUCCESS_CSQ_FILE, csq, "성공 CSQ 영구 저장")

def load_account_fail_csq() -> set:
    if not PERSIST_ACCOUNT_FAIL or not ACCOUNT_FAIL_CSQ_FILE: return set()
    return load_csq_file(ACCOUNT_FAIL_CSQ_FILE)

def save_account_fail_csq(csq: str):
    if not PERSIST_ACCOUNT_FAIL or not ACCOUNT_FAIL_CSQ_FILE: return
    append_csq_file(ACCOUNT_FAIL_CSQ_FILE, csq, "계정 체크 실패 CSQ 영구 저장")

def parse_open_dt_from_text(text: str) -> Optional[datetime]:
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m: hh, mm = int(m.group(1)), int(m.group(2))
    else:
        m2 = re.search(r"(\d{1,2})\s*시(?:(\d{1,2})\s*분)?", text)
        if m2: hh, mm = int(m2.group(1)), int(m2.group(2)) if m2.group(2) else 0
        else: return None
    now = datetime.now()
    open_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if open_dt < now - timedelta(minutes=1): open_dt += timedelta(days=1)
    return open_dt


_list_cycle_count = 0
_last_popup_close_at = 0.0

def goto_list_page(driver: webdriver.Chrome, force_get: bool = False):
    global _list_cycle_count
    if force_get or (not USE_REFRESH_OVER_GET):
        driver.get(LIST_URL); _list_cycle_count = 0
        return
    try:
        if not driver.current_url.startswith(LIST_URL):
            driver.get(LIST_URL); _list_cycle_count = 0
            return
        if _list_cycle_count >= LIST_REOPEN_EVERY:
            driver.get(LIST_URL); _list_cycle_count = 0
        else:
            driver.refresh(); _list_cycle_count += 1
    except Exception:
        driver.get(LIST_URL); _list_cycle_count = 0


def collect_visible_cards(driver: webdriver.Chrome) -> List[Dict]:
    try:
        return driver.execute_script("""
            return Array.from(document.querySelectorAll('.review_item')).map((card, idx) => {
                const rect = card.getBoundingClientRect();
                const tipEl = card.querySelector('.card-body p .ctooltip-text');
                let title = '';
                if (tipEl) { title = (tipEl.innerText || tipEl.textContent || '').trim(); } 
                else {
                    const ctEl = card.querySelector('.card-body p .ctooltip');
                    if (ctEl) {
                        const clone = ctEl.cloneNode(true);
                        const inner = clone.querySelector('.ctooltip-text');
                        if (inner) inner.remove();
                        title = (clone.innerText || clone.textContent || '').trim();
                    } else {
                        const titleEl = card.querySelector('h2, h3, h4, [class*="title"], [class*="name"], .campaign-title, .item-title');
                        title = titleEl ? (titleEl.innerText || '').trim().split('\\n')[0] : '';
                    }
                }
                return {
                    idx: idx,
                    csq: card.getAttribute('data-csq') || '',
                    amount: card.getAttribute('data-amount') || '',
                    jointime: card.getAttribute('data-jointime') || '',
                    title: title,
                    text: card.innerText || '',
                    html: card.innerHTML || '',
                    visible: !!(rect.width || rect.height)
                };
            });
        """) or []
    except Exception: return []

def get_card_element_by_index(driver: webdriver.Chrome, card_index: int):
    try:
        cards = driver.find_elements(By.CSS_SELECTOR, ".review_item")
        if 0 <= int(card_index) < len(cards): return cards[int(card_index)]
    except Exception: pass
    return None

# =========================
# 크롬 드라이버
# =========================
def build_driver(headless: bool = False) -> webdriver.Chrome:
    options = Options()
    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
    }
    if BLOCK_IMAGES: prefs["profile.managed_default_content_settings.images"] = 2
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-save-password-bubble")
    options.add_argument("--disable-features=PasswordLeakDetection,AutofillServerCommunication")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--mute-audio")
    options.add_argument("--lang=ko-KR")
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1280,900")

    driver = webdriver.Chrome(options=options)
    driver.set_window_size(1280, 900)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        blocked = []
        if BLOCK_IMAGES: blocked += ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.ico"]
        if BLOCK_FONTS:  blocked += ["*.woff", "*.woff2", "*.ttf", "*.otf"]
        if BLOCK_MEDIA:  blocked += ["*.mp4", "*.webm", "*.mp3", "*.m4a", "*.avi", "*.mov"]
        if blocked: driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": blocked})
    except Exception: pass
    return driver


def close_site_popups(driver: webdriver.Chrome, force: bool = False):
    global _last_popup_close_at
    now_ts = time.time()
    if (not force) and (now_ts - _last_popup_close_at < POPUP_COOLDOWN_SEC): return
    _last_popup_close_at = now_ts

    try:
        popups = driver.find_elements(By.CSS_SELECTOR, ".review_popup")
        if popups:
            for p in popups:
                try:
                    if not p.is_displayed(): continue
                    try:
                        link_24h = p.find_element(By.XPATH, ".//div[contains(@class,'review_popup_footer')]//a[contains(normalize-space(.),'24시간')]")
                        driver.execute_script("arguments[0].click();", link_24h)
                        time.sleep(0.05)
                    except Exception: pass
                    try:
                        if p.is_displayed():
                            link_close = p.find_element(By.XPATH, ".//div[contains(@class,'review_popup_footer')]//a[contains(normalize-space(.),'닫기')]")
                            driver.execute_script("arguments[0].click();", link_close)
                            time.sleep(0.03)
                    except Exception: pass
                except Exception: pass
            try: driver.execute_script("document.body.style.overflow='auto';")
            except Exception: pass
            return
    except Exception: pass
    try:
        driver.execute_script("""
            document.querySelectorAll('.review_popup').forEach(p => p.remove());
            const area = document.querySelector('.review_popup_area');
            if (area) area.remove();
            document.body.style.overflow='auto';
        """)
    except Exception: pass

def login_dbg(driver: webdriver.Chrome, email: str, password: str):
    driver.get(LOGIN_URL)
    time.sleep(0.6)
    email_el = driver.find_element(By.ID, "email")
    pass_el  = driver.find_element(By.ID, "passwd")
    email_el.clear()
    email_el.send_keys(email)
    pass_el.clear()
    pass_el.send_keys(password + Keys.ENTER)
    time.sleep(1.1)
    close_site_popups(driver)
    if "/usr" not in driver.current_url:
        driver.get(LIST_URL)
        time.sleep(0.9)
    log(f"[OK] 로그인 후 URL: {driver.current_url}")


# =========================
# 모달 확인
# =========================
def _has_show_class(driver: webdriver.Chrome, el_id: str) -> bool:
    try:
        return driver.execute_script(r"""
            const el = document.getElementById(arguments[0]);
            if (!el) return false;
            const cls = (el.getAttribute('class') || '');
            const st  = (el.getAttribute('style') || '');
            const show = cls.split(/\s+/).includes('show');
            const disp_block = st.includes('display: block');
            return !!(show || disp_block) && (el.offsetParent !== null || disp_block);
        """, el_id)
    except Exception: return False

def is_join_accounts_modal_showing(driver: webdriver.Chrome) -> bool: return _has_show_class(driver, "join_accounts_modal")
def is_confirm_modal_showing(driver: webdriver.Chrome) -> bool: return _has_show_class(driver, "confirm_modal")
def is_alert_modal_showing(driver: webdriver.Chrome) -> bool: return _has_show_class(driver, "alert_modal")

def get_alert_msg_text(driver: webdriver.Chrome) -> str:
    try: return (driver.find_element(By.ID, "alert_msg").text or "").strip()
    except Exception: return ""

def wait_for_post_join_event(driver: webdriver.Chrome, timeout: float = 2.0) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        pause_point()
        if is_join_accounts_modal_showing(driver): return "accounts"
        if is_confirm_modal_showing(driver): return "confirm"
        if is_alert_modal_showing(driver) and get_alert_msg_text(driver): return "alert"
        time.sleep(0.02)
    return ""

def handle_result_alert(driver: webdriver.Chrome, current_csq: Optional[str], failed_once: set,
                        current_account_index: Optional[int] = None, available_account_count: int = 0,
                        campaign_name: str = "") -> str:
    t0 = time.time()
    msg = ""
    name_tag = f" [{campaign_name}]" if campaign_name else ""
    while time.time() - t0 < 3.0:
        pause_point()
        try:
            modal = driver.find_element(By.ID, "alert_modal")
            if modal.is_displayed():
                msg = driver.find_element(By.ID, "alert_msg").text.strip()
                if msg: break
        except Exception: pass
        time.sleep(0.02)

    if not msg:
        log(f"[안내] alert 결과 없음 (대기 타임아웃) → {current_csq}{name_tag}")
        return "none"

    log(f"[결과] 결과 메시지: {msg} (csq={current_csq}{name_tag})")
    _log_tuning_summary(msg)
    save_current_page_html(driver, f"alert_{current_csq or 'unknown'}")

    result = "fail"
    if "참여하실 계정을 선택해 주세요" in msg: result = "none"
    elif current_csq and ("캠페인에 참여 되었습니다" in msg or "이미 참여한" in msg):
        save_success_csq(current_csq)
        log(f"[완료] 성공 처리 → 영구 저장 완료: {current_csq}")
        result = "success"
    elif current_csq and "참여 할 수 있는 횟수를 초과하여" in msg:
        if current_account_index == 0 and available_account_count >= 2:
            log("[재시도] 첫번째 계정은 횟수초과 → 두번째 계정 재시도")
            result = "retry_second"
        else:
            failed_once.add(current_csq)
            log(f"[경고] 참여 횟수 초과(영구저장 안 함) → 이번 실행 스킵: {current_csq}{name_tag}")
    elif current_csq and "참여 가능한 시간이 아닙니다" in msg:
        log(f"[경고] 아직 오픈 시간이 아님(서버 알림) — 예측 샷(lead) 값을 조절해 보세요: {current_csq}")
    elif current_csq and ("참여 요청이 많아" in msg or "다시 참여" in msg):
        log(f"[재시도] 서버 혼잡 메시지 감지 → 즉시 재시도: {current_csq}{name_tag}")
        result = "retry_congestion"
    else:
        if current_csq:
            failed_once.add(current_csq)
            log(f"[경고] 성공 아님 → 이번 실행 스킵: {current_csq}")

    try:
        btn = driver.find_element(By.CSS_SELECTOR, "#alert_modal .btn-warning")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.05)
    except Exception: pass
    return result

def handle_account_modal(driver: webdriver.Chrome, current_csq: Optional[str] = None, account_index: int = 0) -> Tuple[bool, int]:
    t0 = time.time()
    while time.time() - t0 < 2.0:
        if is_join_accounts_modal_showing(driver): break
        time.sleep(0.02)
    else: return False, 0
    detected_count = 0
    for attempt in range(1, 4):
        try:
            count = driver.execute_script("const m=document.getElementById('join_accounts_modal'); return m ? m.querySelectorAll('input.member_store_seq').length : 0;")
            if count: detected_count = count
            if account_index >= count: return False, detected_count
            ok = driver.execute_script("""
                const m = document.getElementById('join_accounts_modal');
                if (!m) return false;
                const cbs = Array.from(m.querySelectorAll("input.member_store_seq"));
                cbs.forEach(cb => cb.checked = false);
                const cb = cbs[arguments[0]];
                if (!cb) return false;
                cb.click(); cb.checked = true;
                return cb.checked === true;
            """, account_index)
            if not ok: time.sleep(0.08); continue
            called = driver.execute_script("""
                try {
                    if (typeof join_campaign_confirm === 'function') { join_campaign_confirm(); return true; }
                    const btn = document.getElementById('join_campaign_confirm');
                    if (btn) { btn.click(); return true; }
                    return false;
                } catch(e) { return false; }
            """)
            if not called: time.sleep(0.10); continue
            
            t_evt = time.time()
            while time.time() - t_evt < 4.0:
                if is_confirm_modal_showing(driver) or (is_alert_modal_showing(driver) and get_alert_msg_text(driver)) or not is_join_accounts_modal_showing(driver):
                    return True, detected_count
                time.sleep(0.03)
        except Exception: time.sleep(0.12)
    if current_csq: save_account_fail_csq(current_csq)
    return False, detected_count


_last_confirm_at = 0.0

def try_click_join_button_in_detail_fast(driver: webdriver.Chrome, csq: str) -> str:
    if is_join_accounts_modal_showing(driver) or is_confirm_modal_showing(driver) or is_alert_modal_showing(driver):
        return "CLICKED"
    script = r"""
        const csq = String(arguments[0]);
        let targetBtn = document.getElementById('join_campaign_btn');
        let btnText = '';
        if (targetBtn && targetBtn.offsetParent !== null) {
            btnText = targetBtn.innerText.replace(/\s+/g, ' ').trim();
        } else {
            targetBtn = null;
            for (const el of document.querySelectorAll('button, a, div')) {
                const oc = el.getAttribute('onclick') || '';
                if ((oc.includes('join_campaign_pop') && oc.includes(csq)) || el.getAttribute('data-csq') === csq) {
                    if (el.offsetParent === null) continue;
                    targetBtn = el;
                    btnText = el.innerText.replace(/\s+/g, ' ').trim();
                    break;
                }
            }
        }
        if (!targetBtn) return 'NOT_FOUND';
        if (btnText && (btnText.includes('마감') || btnText.includes('종료') || btnText.includes('불가'))) return 'CLOSED:' + btnText;
        if (btnText && (/\d{1,2}:\d{2}/.test(btnText) || /\d{1,2}시/.test(btnText)) && !btnText.includes('참여')) return 'WAIT:' + btnText;
        try {
            if (typeof join_campaign_pop === 'function') { join_campaign_pop(csq); return 'CLICKED'; }
        } catch (e) {}
        targetBtn.click();
        return 'CLICKED';
    """
    try:
        res = driver.execute_script(script, str(csq))
        return str(res) if res else "NOT_FOUND"
    except Exception: return "NOT_FOUND"

def fast_enter_detail_and_click_join(driver: webdriver.Chrome, card_el, csq: str, timeout: float = 0.6, popup_retry_after: float = 0.12) -> str:
    t0 = time.time()
    popup_cleaned = False
    driver.execute_script("arguments[0].click();", card_el)
    while time.time() - t0 < timeout:
        pause_point()
        res = try_click_join_button_in_detail_fast(driver, csq)
        if res == "CLICKED" or res.startswith("CLOSED") or res.startswith("WAIT"): return res
        if not popup_cleaned and time.time() - t0 >= popup_retry_after:
            try: close_site_popups(driver)
            except Exception: pass
            popup_cleaned = True
        time.sleep(0.01)
    return "NOT_FOUND"

def try_click_join_button_in_detail(driver: webdriver.Chrome, csq: str) -> bool:
    return try_click_join_button_in_detail_fast(driver, csq) == "CLICKED"

def try_open_join_without_button_search(driver: webdriver.Chrome, csq: str) -> bool:
    if is_join_accounts_modal_showing(driver) or is_confirm_modal_showing(driver) or is_alert_modal_showing(driver): return False
    script = r"""
        const csq = String(arguments[0]);
        try {
            if (typeof join_campaign_pop === 'function') { join_campaign_pop(csq); return true; }
            for (const el of document.querySelectorAll('button[onclick], a[onclick]')) {
                const oc = el.getAttribute('onclick') || '';
                if (oc.includes('join_campaign_pop') && (oc.includes(csq) || el.innerText.includes('캠페인 참여'))) {
                    el.click(); return true;
                }
            }
        } catch (e) {}
        return false;
    """
    try: return bool(driver.execute_script(script, str(csq)))
    except Exception: return False

def process_immediate_join_flow(driver: webdriver.Chrome, csq: str, failed_once: set, campaign_name: str = "") -> None:
    name_tag = f" [{campaign_name}]" if campaign_name else ""
    log(f"[진행] JOIN 버튼 클릭 감지(즉시 참여): csq={csq}{name_tag}")
    kind = wait_for_post_join_event(driver, timeout=2.0)
    
    account_try_index = 0
    account_count_hint = 0
    if kind == "accounts" or is_join_accounts_modal_showing(driver):
        handled, detected_count = handle_account_modal(driver, csq, account_index=account_try_index)
        account_count_hint = max(account_count_hint, detected_count)
        if not handled:
            failed_once.add(csq)
            driver.get(LIST_URL)
            time.sleep(0.35)
            close_site_popups(driver, force=True)
            return

    if kind == "confirm" or is_confirm_modal_showing(driver): click_confirm_modal(driver)

    alert_result = handle_result_alert(driver, csq, failed_once, current_account_index=account_try_index, available_account_count=account_count_hint, campaign_name=campaign_name)

    if alert_result == "retry_second":
        if try_click_join_button_in_detail_fast(driver, csq) == "CLICKED" or try_open_join_without_button_search(driver, csq):
            kind2 = wait_for_post_join_event(driver, timeout=2.0)
            if kind2 == "accounts" or is_join_accounts_modal_showing(driver):
                handled2, _ = handle_account_modal(driver, csq, account_index=1)
                if not handled2: failed_once.add(csq)
            if is_confirm_modal_showing(driver): click_confirm_modal(driver)
            handle_result_alert(driver, csq, failed_once, current_account_index=1, available_account_count=account_count_hint, campaign_name=campaign_name)
            
    elif alert_result == "retry_congestion":
        for _ in range(CONGESTION_RETRY_MAX):
            time.sleep(CONGESTION_RETRY_DELAY_SEC)
            if is_confirm_modal_showing(driver): click_confirm_modal(driver)
            else:
                if try_click_join_button_in_detail_fast(driver, csq) != "CLICKED":
                    try_open_join_without_button_search(driver, csq)
            if handle_result_alert(driver, csq, failed_once, current_account_index=account_try_index, available_account_count=account_count_hint, campaign_name=campaign_name) != "retry_congestion":
                break
        else: failed_once.add(csq)

    driver.get(LIST_URL)
    time.sleep(0.35)
    close_site_popups(driver)


# ── 정밀 시계 및 클릭 튜닝 ─────────────────────────────────────
_CLICK_TIMING_CTX: Optional[Dict] = None   

class NaverClock:
    def __init__(self):
        self.Ready = False
        self.RoundTripMs = 0.0
        self.key = None
        self.anchor = None
        self.anchorTicks = 0.0
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://search.naver.com/"})

    @property
    def Age(self) -> float: return time.perf_counter() - self.anchorTicks if self.Ready else float("inf")
    @property
    def Fresh(self) -> bool: return self.Ready and self.Age < 120.0
    @property
    def Now(self) -> datetime: return self.anchor + timedelta(seconds=(time.perf_counter() - self.anchorTicks))

    def Apply(self, sample: dict):
        self.anchor = sample["Server"] + timedelta(milliseconds=sample["RoundTripMs"] / 2.0)
        self.anchorTicks = sample["Received"]
        self.RoundTripMs = sample["RoundTripMs"]
        self.Ready = True

    def _get(self, url: str) -> str:
        resp = self.http.get(url, timeout=4)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def ParseKey(html: str) -> str:
        m = re.search(r'"passportKey"\s*:\s*"([A-Za-z0-9_-]+)"', html)
        if not m: raise ValueError("네이버 시계 연결 방식 변경됨")
        return m.group(1)

    @staticmethod
    def ParseTime(text: str) -> datetime:
        m = re.search(r'oneClickClock\(\s*"(\d{4}/\d{2}/\d{2}/\d{2}/\d{2}/\d{2}/\d{3})"\s*\)\s*;?$', text.strip())
        p = m.group(1).split("/")
        return datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5]), int(p[6]) * 1000)

    def Fetch(self) -> dict:
        if self.key is None: self.key = self.ParseKey(self._get("https://search.naver.com/search.naver?query=%EB%84%A4%EC%9D%B4%EB%B2%84%EC%8B%9C%EA%B3%84"))
        url = "https://m.search.naver.com/p/csearch/dcontent/util/time.naver?passportKey=" + urllib.parse.quote(self.key) + "&_format=yyyy%2FMM%2Fdd%2FHH%2Fmm%2Fss%2FSSS&_callback=oneClickClock&_=" + uuid.uuid4().hex
        
        def _req():
            t0 = time.perf_counter()
            text = self._get(url)
            t1 = time.perf_counter()
            rtt = (t1 - t0) * 1000.0
            if rtt > 2000.0: raise ValueError("지연 초과")
            return {"Server": self.ParseTime(text), "Received": t1, "RoundTripMs": rtt}
            
        try: _req()
        except Exception: 
            try: self.key = self.ParseKey(self._get("https://search.naver.com/search.naver?query=%EB%84%A4%EC%9D%B4%EB%B2%84%EC%8B%9C%EA%B3%84"))
            except Exception: pass
        
        best = None
        for i in range(3):
            try:
                smp = _req()
                if best is None or smp["RoundTripMs"] < best["RoundTripMs"]: best = smp
            except Exception: pass
            if i < 2: time.sleep(0.05)
        if best is None: raise RuntimeError("네이버 시각 실패")
        return best

_naver_clock = NaverClock()

def _sync_naver_clock(reason: str) -> bool:
    try: _naver_clock.Apply(_naver_clock.Fetch())
    except Exception as e:
        log(f"[경고] 네이버 시각 동기화 실패({reason}) → PC 시계로 진행: {e}")
        return False
    return True

def _precise_now() -> datetime:
    return _naver_clock.Now if _naver_clock.Ready else datetime.now()


# ⭐ 튜닝 파일에 'auto' 설정 설명 추가
_TUNING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "click_tuning.txt")
_TUNING_DEFAULT_TEXT = """# 정각 클릭 튜닝 — 메모장으로 고치고 저장하면 다음 참여부터 바로 적용됩니다 (재시작 불필요)
#
# lead_ms : auto로 입력하면 현재 서버 핑을 측정하여 자동으로 최적값(핑의 절반)을 적용합니다.
#           (숫자를 직접 입력하면 해당 숫자로 고정됩니다. 예: 40)
# inpage  : 1 = 브라우저 안에서 정각에 직접 클릭 (파이썬→브라우저 전달 지연이 없어짐. 실전 검증 전)
#           0 = 파이썬이 정각에 클릭 명령을 보냄 (기존 방식, 검증됨)
# warmup  : 1 = 정각 2~3초 전에 사이트와의 연결을 한 번 확인해 둠 (연결이 식어서 첫 요청이 느려지는 것 방지)
lead_ms = auto
inpage = 0
warmup = 1
"""
_LAST_TIMED_CLICK: Optional[Dict] = None   

def _read_tuning() -> Dict:
    cfg = {"lead_ms": "auto", "inpage": False, "warmup": True, "warn": ""}
    try:
        if not os.path.exists(_TUNING_FILE):
            with open(_TUNING_FILE, "w", encoding="utf-8") as f: f.write(_TUNING_DEFAULT_TEXT)
            return cfg
        with open(_TUNING_FILE, "r", encoding="utf-8-sig") as f: lines = f.read().splitlines()
    except Exception as e:
        cfg["warn"] = f"읽기 실패로 기본값 사용: {e}"
        return cfg

    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line: continue
        key, val = [x.strip() for x in line.split("=", 1)]
        if key.lower() == "lead_ms":
            if val.lower() == "auto":
                cfg["lead_ms"] = "auto"
            else:
                try: cfg["lead_ms"] = max(-100.0, min(500.0, float(val)))
                except ValueError: pass
        elif key.lower() == "inpage": cfg["inpage"] = val in ("1", "true", "on", "yes")
        elif key.lower() == "warmup": cfg["warmup"] = val in ("1", "true", "on", "yes")
    return cfg

def _naive_epoch_ms(dt: datetime) -> float:
    return (dt - datetime(1970, 1, 1)).total_seconds() * 1000.0

_WARM_JS = r"""
window.__gt_warm = {done: false};
(async () => {
    const w = window.__gt_warm;
    const one = async () => {
        const ac = new AbortController();
        const to = setTimeout(() => ac.abort(), 900);
        const t0 = performance.now();
        try {
            const r = await fetch(location.origin + '/', {method: 'HEAD', cache: 'no-store', signal: ac.signal});
            clearTimeout(to); return [Math.round((performance.now() - t0) * 10) / 10, r.status];
        } catch (e) { clearTimeout(to); return [Math.round((performance.now() - t0) * 10) / 10, 'err']; }
    };
    w.a = await one(); w.b = await one(); w.done = true;
})(); return true;
"""
def _warm_connection(driver):
    try: driver.execute_script(_WARM_JS)
    except Exception: pass

_JS_CLICK_FN = r"""
function __gt_click(nowFn) {
    try {
        const el = document.getElementById('confirm_modal');
        if (!el || el.offsetParent === null) return ['not_shown', nowFn()];
        const t = nowFn();
        if (typeof confirm_func === 'function') { confirm_func(); return ['clicked', t]; }
        const btn = document.querySelector("#confirm_modal button.butn.butn-yellow");
        if (btn) { btn.click(); return ['clicked', t]; }
        return ['no_handler', t];
    } catch (e) { return ['error', nowFn()]; }
}
"""
_CONFIRM_CLICK_JS = _JS_CLICK_FN + "\nreturn __gt_click(() => Date.now());"

def _sync_browser_clock(driver, samples: int = 6):
    best = None
    for _ in range(samples):
        a = time.perf_counter(); na = _precise_now()
        js = driver.execute_script("return performance.timeOrigin + performance.now();")
        nb = _precise_now(); b = time.perf_counter()
        rtt = b - a
        mid = _naive_epoch_ms(na) + (_naive_epoch_ms(nb) - _naive_epoch_ms(na)) / 2.0
        if best is None or rtt < best[1]: best = (float(js) - mid, rtt)
    return best

def _arm_inpage_click(driver, target: datetime, lead_s: float) -> Optional[Dict]:
    if STOP_SIGNAL.is_set() or PAUSED.is_set(): return None
    try:
        off_ms, rtt_s = _sync_browser_clock(driver)
        fire_at = _naive_epoch_ms(target) - lead_s * 1000.0 + off_ms
        js = _JS_CLICK_FN + r"""
        const fireAt = arguments[0];
        window.__gt = {fired: false, status: null, t: null};
        const perfNow = () => performance.timeOrigin + performance.now();
        function tick() {
            const rem = fireAt - perfNow();
            if (rem <= 0) {
                if(!window.__gt.fired) { window.__gt.fired = true; const r = __gt_click(perfNow); window.__gt.status = r[0]; window.__gt.t = r[1]; }
                return;
            }
            if (rem > 30) { setTimeout(tick, Math.max(1, rem - 25)); return; }
            while (perfNow() < fireAt) {}
            if(!window.__gt.fired) { window.__gt.fired = true; const r = __gt_click(perfNow); window.__gt.status = r[0]; window.__gt.t = r[1]; }
        }
        tick(); return true;
        """
        driver.execute_script(js, fire_at)
        return {"offset_ms": off_ms, "rtt_ms": rtt_s * 1000.0, "fire_at": fire_at}
    except Exception as e:
        log(f"[경고] 브라우저 예약 실패 → 파이썬 방식으로 진행: {e}")
        return None

def _log_tuning_summary(msg: str):
    global _LAST_TIMED_CLICK
    rec = _LAST_TIMED_CLICK
    if not rec or time.time() - rec.get("t", 0) > 15.0: return
    _LAST_TIMED_CLICK = None
    if "참여 가능한 시간이 아닙니다" in msg: label = "너무 일찍 도착 (lead를 낮추세요)"
    elif "참여 요청이 많아" in msg: label = "혼잡 (서버가 거절)"
    elif "캠페인에 참여 되었습니다" in msg: label = "성공"
    elif "이미 참여" in msg: label = "이미 참여"
    elif "마감" in msg: label = "마감"
    else: label = "기타"
    log(f"[튜닝] lead {rec['lead_ms']:g}ms | 클릭 오픈대비 {rec['after_open_s']:+.3f}s ({rec['path']}) | 결과: {label}")


def wait_until_server_second(driver: webdriver.Chrome, target_open_dt_local: datetime) -> bool:
    global _CLICK_TIMING_CTX
    tun = _read_tuning()
    
    # ⭐ 핑 기반 예측 샷 자동 적용 
    if str(tun["lead_ms"]).lower() == "auto":
        effective_lead_ms = (CURRENT_PING_MS / 2.0) if CURRENT_PING_MS > 0 else 20.0
        note = f"자동 영점(핑 {CURRENT_PING_MS:.1f}ms 기준)"
    else:
        effective_lead_ms = float(tun["lead_ms"])
        note = ""

    lead = effective_lead_ms / 1000.0
    remain0 = (target_open_dt_local - _precise_now()).total_seconds()
    
    if remain0 > 1.5:
        if tun["warn"]: log(f"[경고] click_tuning.txt: {tun['warn']}")
        mode_str = "자동 설정(Auto)" if str(tun["lead_ms"]).lower() == "auto" else f"{effective_lead_ms:g}ms 고정"
        log(f"[설정] 클릭 lead {mode_str} | 브라우저 예약 {'켜짐' if tun['inpage'] else '꺼짐'} | 연결 예열 {'켜짐' if tun['warmup'] else '꺼짐'}")

    last_sync_try = time.perf_counter()
    if _naver_clock.Fresh: last_sync_try -= _naver_clock.Age
    elif remain0 > 1.5:
        _sync_naver_clock("최초")
        last_sync_try = time.perf_counter()

    warmed, armed_tried, arm_info = False, False, None
    try:
        while True:
            pause_point()
            remain = (target_open_dt_local - _precise_now()).total_seconds() - lead
            if remain <= 0: break

            if remain > 12.0 and (time.perf_counter() - last_sync_try) >= 60.0:
                last_sync_try = time.perf_counter(); _sync_naver_clock("재동기화"); continue
            if tun["warmup"] and not warmed and 1.2 < remain <= 3.0:
                warmed = True; _warm_connection(driver)
            if tun["inpage"] and not armed_tried and 0.15 < remain <= 0.6:
                armed_tried = True; arm_info = _arm_inpage_click(driver, target_open_dt_local, lead); continue

            if remain > 5.0: time.sleep(1.0)
            elif remain > 1.0: time.sleep(0.1)
            elif remain > 0.1: time.sleep(0.01)
            elif remain > 0.02: time.sleep(0.001)
    except BaseException:
        if arm_info is not None:
            try: driver.execute_script("if(window.__gt){window.__gt.fired=true;}")
            except Exception: pass
        raise

    _CLICK_TIMING_CTX = {
        "target": target_open_dt_local, "reached_dt": _precise_now(), "reached_precise": _naver_clock.Ready,
        "lead_ms": effective_lead_ms, "note": note, "inpage": arm_info, "warmed": warmed,
    }
    return True

def click_confirm_modal(driver: webdriver.Chrome) -> bool:
    global _last_confirm_at, _CLICK_TIMING_CTX, _LAST_TIMED_CLICK
    ctx = _CLICK_TIMING_CTX
    _CLICK_TIMING_CTX = None   

    if ctx is not None and (time.time() - _last_confirm_at) >= 0.2:
        t_click0 = time.time()
        if ctx.get("inpage"):
            status, t_ms, how = "error", None, "none"
            t_end = time.perf_counter() + 0.35
            while time.perf_counter() < t_end:
                try:
                    r = driver.execute_script("const g = window.__gt; return (g && g.status) ? [g.status, g.t] : null;")
                    if r: status, t_ms, how = r[0], r[1], "inpage"; break
                except Exception: pass
                time.sleep(0.003)
            if how == "none":
                try: 
                    r = driver.execute_script(_JS_CLICK_FN + "\nif(window.__gt && window.__gt.fired) return ['already', null]; if(window.__gt) window.__gt.fired=true; return __gt_click(() => performance.timeOrigin+performance.now());")
                    if r and r[0] != "already": status, t_ms, how = r[0], r[1], "fallback"
                except Exception: pass
            
            exec_ms = int((time.time() - t_click0) * 1000)
            ai = ctx["inpage"]
            disp_dt = (datetime(1970, 1, 1) + timedelta(milliseconds=(float(t_ms) - ai["offset_ms"]))) if t_ms else None
            path_label, extra = ("브라우저 내부 예약", f"(오차 ±{ai['rtt_ms']/2:.1f}ms)") if how == "inpage" else ("예약 실패 → 파이썬 보조", "")
        else:
            na = _precise_now()
            try: res = driver.execute_script(_CONFIRM_CLICK_JS)
            except Exception: res = None
            nb = _precise_now()
            exec_ms = int((time.time() - t_click0) * 1000)
            status = res[0] if res else "error"
            disp_dt = na + (nb - na) / 2
            path_label, extra = "파이썬 직접", f"(측정 오차 ±{(nb-na).total_seconds()*500:.0f}ms)"

        if status == "clicked":
            _last_confirm_at = t_click0
            target, reached = ctx["target"], ctx["reached_dt"]
            src = "네이버" if ctx.get("reached_precise") else "PC시계"
            log(f"[대기] 목표 도달: {src} {reached.strftime('%H:%M:%S.%f')[:-3]} (오픈대비 {(reached-target).total_seconds():+.3f}s, lead={ctx.get('lead_ms', 0):g}ms) {ctx.get('note','')}")
            diff = (disp_dt - target).total_seconds() if disp_dt else 0
            if disp_dt: log(f"[클릭] 참여확인: {src} {disp_dt.strftime('%H:%M:%S.%f')[:-3]} | 오픈대비 {diff:+.3f}s | {path_label} {extra}")
            
            _LAST_TIMED_CLICK = {"lead_ms": ctx.get("lead_ms", 0.0), "after_open_s": diff, "path": path_label, "t": time.time()}
            
            t_hide = time.time()
            while time.time() - t_hide < 2.0:
                if not is_confirm_modal_showing(driver): break
                time.sleep(0.03)
            return True

    t0 = time.time()
    while time.time() - t0 < 2.0:
        if is_confirm_modal_showing(driver): break
        time.sleep(0.02)
    else: return False

    if time.time() - _last_confirm_at < 0.2: return True
    _last_confirm_at = time.time()

    driver.execute_script("try{if(typeof confirm_func === 'function'){confirm_func();return true;}const b=document.querySelector('#confirm_modal button.butn.butn-yellow');if(b){b.click();return true;}return false;}catch(e){return false;}")
    return True


def wait_and_apply_in_detail(driver: webdriver.Chrome, current_csq: str, failed_once: set, target_open_dt: Optional[datetime], is_target: bool = False, campaign_name: str = "") -> bool:
    t0 = time.time()
    confirm_armed, prejoin_started, account_try_index, account_count_hint, congestion_retry_count = False, False, 0, 0, 0
    name_tag = f" [{campaign_name}]" if campaign_name else ""
    last_prejoin = 0.0

    while True:
        pause_point()
        close_site_popups(driver)

        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if target_open_dt is None: target_open_dt = parse_open_dt_from_text(body_text)
            if target_open_dt:
                sec_to_open = (target_open_dt - datetime.now()).total_seconds()
                if not is_target and sec_to_open > (HOT_WINDOW_SEC + 30) and ("참여가능" not in body_text):
                    log(f"[복귀] 오픈시간 멀어짐 → 복귀: {current_csq}"); driver.get(LIST_URL); return False
        except Exception: sec_to_open = None

        if is_alert_modal_showing(driver) and get_alert_msg_text(driver):
            res = handle_result_alert(driver, current_csq, failed_once, account_try_index, account_count_hint, campaign_name)
            if res == "retry_second": account_try_index = 1; prejoin_started = False; confirm_armed = False; time.sleep(0.1); continue
            if res == "retry_congestion":
                congestion_retry_count += 1
                if congestion_retry_count <= CONGESTION_RETRY_MAX:
                    time.sleep(CONGESTION_RETRY_DELAY_SEC); prejoin_started = False; confirm_armed = False; continue
                else: failed_once.add(current_csq)
            driver.get(LIST_URL); return True

        if is_confirm_modal_showing(driver) and not confirm_armed:
            if target_open_dt: wait_until_server_second(driver, target_open_dt)
            click_confirm_modal(driver); confirm_armed = True; continue

        if confirm_armed: time.sleep(get_adaptive_poll_sec(sec_to_open)); continue

        if is_join_accounts_modal_showing(driver):
            ok, cnt = handle_account_modal(driver, current_csq, account_try_index); account_count_hint = max(account_count_hint, cnt)
            if not ok: failed_once.add(current_csq); driver.get(LIST_URL); return False
            continue

        now = time.time()
        if not prejoin_started or (now - last_prejoin >= 0.50):
            last_prejoin = now
            if try_open_join_without_button_search(driver, current_csq) or try_click_join_button_in_detail(driver, current_csq):
                prejoin_started = True
                save_current_page_html(driver, f"detail_{current_csq}_prejoin_clicked")
                kind = wait_for_post_join_event(driver, 2.0)
                if kind == "alert": continue
                if kind == "accounts": continue
                if kind == "confirm": continue

        if not is_target and time.time() - t0 >= DETAIL_MAX_WAIT_SEC:
            log(f"[복귀] 대기 타임아웃 → 복귀: {current_csq}"); driver.get(LIST_URL); return False

        time.sleep(get_adaptive_poll_sec(sec_to_open))

def enter_detail_and_wait(driver: webdriver.Chrome, card_el, csq: str, failed_once: set, target_open_dt: Optional[datetime], is_target: bool = False, title: str = "") -> bool:
    try:
        driver.execute_script("arguments[0].click();", card_el)
        time.sleep(0.8)
        close_site_popups(driver, force=True)
        log(f"[진입] 상세 선점 진입: csq={csq} [{title}]")
        return wait_and_apply_in_detail(driver, csq, failed_once, target_open_dt, is_target=is_target, campaign_name=title)
    except StopRequested: raise
    except Exception as e:
        log(f"[오류] 진입 실패: {e}")
        failed_once.add(csq)
        try: driver.get(LIST_URL)
        except Exception: pass
        return False

# =========================
# 스캔 + 후보 선정
# =========================
def scan_and_pick(driver: webdriver.Chrome, failed_once: set) -> Tuple[Optional[Dict], Optional[Dict]]:
    success_csq = load_success_csq()
    account_fail_csq = load_account_fail_csq()
    t_scan0 = time.time()
    now = datetime.now()

    pause_point()
    goto_list_page(driver)
    time.sleep(0.9)
    close_site_popups(driver)
    
    picked_now = None
    picked_soon = None
    seen_csq = set()

    def type_priority(v: str) -> int: return 0 if v == "REAL" else 1

    def inspect_visible_cards(stage_label: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        nonlocal picked_now, picked_soon
        cards = collect_visible_cards(driver)
        local_best_now, local_best_soon = picked_now, picked_soon
        
        global TARGET_CSQ
        target_list = [t.strip() for t in TARGET_CSQ.split(",") if t.strip()]
        target_open_now_matches, target_hot_matches = [], []

        for item_raw in cards:
            try:
                csq = (item_raw.get("csq") or "").strip()
                if not csq or csq in seen_csq: continue
                seen_csq.add(csq)

                text = item_raw.get("text") or ""
                html = item_raw.get("html") or ""
                amount_attr = item_raw.get("amount") or ""
                
                open_dt = parse_open_dt_from_text(text)
                secs_to_open = (open_dt - now).total_seconds() if open_dt else None

                if target_list and csq in target_list:
                    if csq in success_csq: continue
                    is_open_now = ("참여가능" in text and ("진행중" in text or "대기중" in text))
                    if open_dt and secs_to_open > 0: is_open_now = False

                    if is_open_now:
                        target_open_now_matches.append({"csq": csq, "card_index": item_raw.get("idx"), "title": item_raw.get("title") or ""})
                    else:
                        if secs_to_open is None or secs_to_open <= HOT_WINDOW_SEC:
                            target_hot_matches.append({
                                "csq": csq, "card_index": item_raw.get("idx"), "title": item_raw.get("title") or "",
                                "open_dt": open_dt, "secs_to_open": secs_to_open,
                            })
                    continue

                ctype = detect_type(text, html)
                price = resolve_card_price(amount_attr, text)
                shop = detect_shop(text, html)
                eligible_base = True

                if csq in success_csq or csq in account_fail_csq or csq in failed_once: eligible_base = False
                elif ctype == "ETC" and price < MIN_PRICE_ETC: eligible_base = False
                elif ctype == "REAL" and price < MIN_PRICE_REAL: eligible_base = False
                elif ctype == "UNKNOWN": eligible_base = False
                if eligible_base and not is_allowed_deposit_mode(ACTIVE_SITE_KEY, ctype, detect_deposit_mode(text, html)): eligible_base = False

                item = {
                    "card_index": item_raw.get("idx"), "csq": csq, "title": item_raw.get("title") or "",
                    "type": ctype, "shop": shop, "price": price, "open_dt": open_dt, "secs_to_open": secs_to_open,
                }

                def sort_key(i: dict) -> tuple: return (type_priority(i["type"]), 0 if i["shop"] == "NAVER" else 1, -i["price"])

                eligible_now = eligible_base and ("참여가능" in text and ("진행중" in text or "대기중" in text))
                eligible_soon = eligible_base and open_dt and secs_to_open is not None and (0 < secs_to_open <= HOT_WINDOW_SEC)

                if eligible_now:
                    if local_best_now is None or sort_key(item) < sort_key(local_best_now): local_best_now = item
                elif eligible_soon:
                    if local_best_soon is None: local_best_soon = item
                    else:
                        cur_key = (item["open_dt"], type_priority(item["type"]), 0 if item["shop"] == "NAVER" else 1, -item["price"])
                        prev_key = (local_best_soon["open_dt"], type_priority(local_best_soon["type"]), 0 if local_best_soon["shop"] == "NAVER" else 1, -local_best_soon["price"])
                        if cur_key < prev_key: local_best_soon = item

            except Exception: pass

        if target_open_now_matches:
            best = min(target_open_now_matches, key=lambda m: target_list.index(m["csq"]))
            log(f"[타겟] [타겟 발견] {best['csq']} 즉시 참여 가능 → 즉시 참여 시도!")
            picked_now = {"csq": best["csq"], "card_index": best["card_index"], "price": 0, "type": "TARGET", "title": best["title"]}
            return picked_now, local_best_soon

        if target_hot_matches:
            best = min(target_hot_matches, key=lambda m: target_list.index(m["csq"]))
            log(f"[타겟] [타겟 발견] {best['csq']} → 오픈 임박, 최우선 상세 진입!")
            picked_soon = {"csq": best["csq"], "card_index": best["card_index"], "price": 0, "type": "TARGET", "title": best["title"], "open_dt": best["open_dt"]}
            return local_best_now, picked_soon

        return local_best_now, local_best_soon

    now_item, soon_item = inspect_visible_cards("top")
    if now_item or (soon_item and soon_item.get("type") == "TARGET"): return now_item, soon_item

    for i in range(MAX_SCROLL):
        pause_point()
        driver.execute_script("window.scrollBy(0, 1500);")
        time.sleep(0.30)
        now_item, soon_item = inspect_visible_cards(f"scroll_{i+1}")
        if now_item or (soon_item and soon_item.get("type") == "TARGET"): return now_item, soon_item

    return picked_now, picked_soon


# =========================
# 메인
# =========================
def main():
    apply_site_config(choose_site())
    
    if not os.path.exists(SUCCESS_CSQ_FILE): open(SUCCESS_CSQ_FILE, "w").close()
    if PERSIST_ACCOUNT_FAIL and ACCOUNT_FAIL_CSQ_FILE and not os.path.exists(ACCOUNT_FAIL_CSQ_FILE):
        open(ACCOUNT_FAIL_CSQ_FILE, "w").close()

    email = os.environ.get("DBG_EMAIL") or input(f"[{SITE_NAME}] 이메일 입력: ").strip()
    password = os.environ.get("DBG_PASS") or os.environ.get("DBG_PASSWORD")
    if not password: password = getpass("비밀번호 입력: ").strip()

    driver = build_driver(headless=HEADLESS)
    failed_once = set()

    try:
        login_dbg(driver, email, password)
        setup_pause_handlers()
        start_ping_monitor() # ⭐ 백그라운드 핑 모니터링 시작
        log(f"[진행] 자동 감시 시작 [{SITE_NAME}]")

        loop_no = 0
        while True:
            loop_no += 1
            begin_loop_log(loop_no)
            loop_status = "done"
            try:
                pause_point()
                picked_now, picked_soon = scan_and_pick(driver, failed_once)

                if picked_soon is not None:
                    csq = picked_soon["csq"]
                    card_el = get_card_element_by_index(driver, picked_soon["card_index"])
                    if card_el is None:
                        driver.get(LIST_URL); time.sleep(0.25); continue
                    enter_detail_and_wait(driver, card_el, csq, failed_once, picked_soon.get("open_dt"), title=picked_soon.get("title") or "")
                    time.sleep(0.25)
                    continue

                if picked_now is not None:
                    csq = picked_now["csq"]
                    card_el = get_card_element_by_index(driver, picked_now["card_index"])
                    if card_el is None:
                        driver.get(LIST_URL); time.sleep(0.25); continue
                    try:
                        opened = fast_enter_detail_and_click_join(driver, card_el, csq, timeout=0.6, popup_retry_after=0.12)
                        if opened == "CLICKED": process_immediate_join_flow(driver, csq, failed_once, campaign_name=picked_now.get("title") or "")
                        elif opened.startswith("CLOSED"): failed_once.add(csq); driver.get(LIST_URL); time.sleep(0.35)
                        elif opened.startswith("WAIT"): wait_and_apply_in_detail(driver, csq, failed_once, parse_open_dt_from_text(opened), campaign_name=picked_now.get("title") or "")
                        else: failed_once.add(csq); driver.get(LIST_URL); time.sleep(0.35)
                    except StopRequested: raise
                    except Exception: failed_once.add(csq); driver.get(LIST_URL); time.sleep(0.35)
                    continue

                time.sleep(BASE_REFRESH_INTERVAL)

            finally:
                end_loop_log(loop_status)

    except KeyboardInterrupt: pass
    finally:
        stop_ping_monitor() # ⭐ 백그라운드 핑 모니터링 종료
        quit_ok = True
        try: driver.quit()
        except Exception: quit_ok = False
        if not quit_ok:
            try:
                import psutil
                parent = psutil.Process(driver.service.process.pid)
                for p in parent.children(recursive=True):
                    try: p.kill()
                    except Exception: pass
                parent.kill()
            except Exception: pass


if __name__ == "__main__":
    main()
