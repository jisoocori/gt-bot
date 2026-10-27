# -*- coding: utf-8 -*-
LOGIC_LAST_MODIFIED = "2026-10-26"  # ⭐ 이 파일 수정할 때마다 갱신

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

BASE_REFRESH_INTERVAL = 2          # 평소 스캔 주기(초)
HOT_WINDOW_SEC        = 100        # 오픈 100초 이내면 상세 선점

DETAIL_POLL_SEC        = 0.05      # 상세 체크 간격 (브라우저 DOM 감시, 오픈 임박 시 사용)
DETAIL_HTML_POLL_SEC   = 0.15      # requests로 상세 HTML 재조회 간격
DETAIL_MAX_WAIT_SEC    = 75        # 선점 후 최대 대기

# ⭐ 오픈까지 남은 시간에 따른 적응형 폴링 간격 (오픈이 멀수록 느슨하게 체크)
DETAIL_POLL_SEC_FAR    = 10.0      # 오픈까지 60초 초과 남았을 때
DETAIL_POLL_SEC_MID    = 0.3       # 오픈까지 10~60초 남았을 때
DETAIL_POLL_SEC_NEAR   = DETAIL_POLL_SEC  # 오픈까지 10초 이하 (기존 0.05초 유지, 정확도 최우선)
DETAIL_POLL_NEAR_THRESHOLD_SEC = 10
DETAIL_POLL_MID_THRESHOLD_SEC  = 60


def get_adaptive_poll_sec(sec_to_open: Optional[float]) -> float:
    """오픈까지 남은 시간(sec_to_open)에 따라 폴링 간격을 동적으로 반환.
    남은 시간을 모를 때는 안전하게 가장 빠른 간격(NEAR)을 사용."""
    if sec_to_open is None:
        return DETAIL_POLL_SEC_NEAR
    if sec_to_open > DETAIL_POLL_MID_THRESHOLD_SEC:
        return DETAIL_POLL_SEC_FAR
    if sec_to_open > DETAIL_POLL_NEAR_THRESHOLD_SEC:
        return DETAIL_POLL_SEC_MID
    return DETAIL_POLL_SEC_NEAR
REQUEST_TIMEOUT_SEC    = 2.5
CONFIRM_CLICK_LEAD_SEC = 0.0       # 참여확인 클릭을 정각(네이버 시각 기준)보다 몇 초 먼저 보낼지.
                                   # 0.0 = 정각에 딱 맞춤(친구 프로그램과 동일). 로그의 [클릭] 줄(오픈 대비 +x.xxxs)과
                                   # 결과 메시지를 보면서 조정 — "참여 가능한 시간이 아닙니다"가 나오면 값을 줄이고,
                                   # 계속 "마감"이면 0.01~0.03 정도씩 늘려보세요.
CONGESTION_RETRY_MAX       = 5     # "참여 요청이 많아 다시 참여" 메시지일 때 최대 재시도 횟수
CONGESTION_RETRY_DELAY_SEC = 0.3   # 혼잡 재시도 간 대기 간격

MAX_SCROLL = 10
HEADLESS = True
LOG_SAVE = False   # True=텍스트 로그+HTML 저장 / False=둘 다 저장 안함

# ⭐ 최우선 타겟 변수 추가 (GUI에서 실시간으로 값 주입)
TARGET_CSQ = ""

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
# 로그 유틸 (ms 단위)
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
    if not LOG_SAVE:
        return
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

    if not LOG_SAVE or not CURRENT_LOOP_LOG_FILE:
        return

    try:
        with open(CURRENT_LOOP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f"ended_at={datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
            f.write(f"status={status}\n")
    except Exception:
        pass
    finally:
        CURRENT_LOOP_LOG_FILE = None
        CURRENT_LOOP_DIR = None
        CURRENT_HTML_SEQ = 0


def save_html(label: str, html: str) -> Optional[str]:
    global CURRENT_HTML_SEQ

    if not LOG_SAVE:
        return None
    if not CURRENT_LOOP_DIR or not html:
        return None

    try:
        CURRENT_HTML_SEQ += 1
        filename = f"{CURRENT_HTML_SEQ:03d}_{_safe_fs_name(label)}.html"
        path = os.path.join(CURRENT_LOOP_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"[저장] HTML 저장: {filename}")
        return path
    except Exception as e:
        print(f"[{ts_ms()}]{site_log_tag()} / [경고] HTML 저장 실패: {e}")
        return None


def save_current_page_html(driver: webdriver.Chrome, label: str) -> Optional[str]:
    # ⭐ 저장 설정이 꺼져 있으면 page_source(페이지 전체 가져오기, 30~170ms)를 아예 읽지 않음
    if not LOG_SAVE:
        return None
    try:
        return save_html(label, driver.page_source or "")
    except Exception as e:
        log(f"[경고] 현재 페이지 HTML 저장 실패: {e}")
        return None


def _append_loop_log_line(line: str):
    if not LOG_SAVE:
        return
    if not CURRENT_LOOP_LOG_FILE:
        return
    try:
        with open(CURRENT_LOOP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


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
PAUSED = threading.Event()       # set()이면 일시정지 상태
STOP_SIGNAL = threading.Event()  # set()이면 종료 요청 상태

class StopRequested(Exception):
    """GUI 종료 버튼 시 모든 대기 루프를 즉시 탈출하기 위한 예외"""
    pass

def _toggle_pause():
    if PAUSED.is_set():
        PAUSED.clear()
        log("[진행] 재개")
    else:
        PAUSED.set()
        log("[일시정지] 일시정지 (Ctrl+Z 다시 누르면 재개)")

def pause_point():
    """일시정지면 블로킹, 종료 요청이면 StopRequested 예외 발생"""
    if STOP_SIGNAL.is_set():
        raise StopRequested("종료 요청")
    while PAUSED.is_set():
        if STOP_SIGNAL.is_set():
            raise StopRequested("종료 요청")
        time.sleep(0.20)

def setup_pause_handlers():
    """Ctrl+Z로 pause 토글(가능한 환경에서)"""
    try:
        if hasattr(signal, "SIGTSTP"):
            def _on_tstp(signum, frame):
                _toggle_pause()
            signal.signal(signal.SIGTSTP, _on_tstp)
    except Exception:
        pass

    if platform.system().lower().startswith("win"):
        try:
            import msvcrt

            def _win_listener():
                while True:
                    try:
                        ch = msvcrt.getwch()
                    except Exception:
                        break

                    if ch == "\x1a":
                        _toggle_pause()

                    elif ch.lower() == "c":
                        log("[일시정지] 'c' 키 종료 요청")
                        os._exit(0)

            th = threading.Thread(target=_win_listener, daemon=True)
            th.start()
        except Exception:
            pass


# =========================
# 일반 유틸
# =========================
def safe_text(el) -> str:
    try:
        return el.text or ""
    except Exception:
        return ""

def extract_price(text: str) -> int:
    m = re.search(r"([\d,]+)\s*원", text)
    return int(m.group(1).replace(",", "")) if m else 0


def parse_amount_attr(value: str) -> int:
    raw = str(value or "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else 0


def resolve_card_price(amount_attr: str, text: str) -> int:
    amount_price = parse_amount_attr(amount_attr)
    if amount_price > 0:
        return amount_price
    return extract_price(text)

def detect_type(text: str, html: str = "") -> str:
    blob = ((text or "") + " " + (html or "")).strip()
    if "실배송" in blob:
        return "REAL"
    if "기타배송" in blob:
        return "ETC"
    return "UNKNOWN"


def detect_shop(text: str, html: str = "") -> str:
    # 카드 HTML에서 쇼핑몰 이미지 패턴으로 판별
    # ex) <img src="/resource/img/shop/naver.png" alt="Naver">
    blob = ((text or "") + " " + (html or "")).strip()
    if "img/shop/naver.png" in blob:
        return "NAVER"
    return "OTHER"


def detect_deposit_mode(text: str, html: str = "") -> str:
    blob = ((text or "") + " " + (html or "")).strip()
    normalized = re.sub(r"\s+", "", blob).upper()

    if ("트루펄스" in normalized) or ("TRUEPULSE" in normalized):
        return "TRUEPULSE"
    if ("업체입금" in blob) or ("type_box_deposit_n" in blob):
        return "VENDOR"
    if ("굿투입금" in blob) or ("type_box_deposit_y" in blob):
        return "GOODTO"
    return "UNKNOWN"


def is_allowed_deposit_mode(site_key: str, campaign_type: str, deposit_mode: str) -> bool:
    allowed_by_type = SITE_CONFIGS.get(site_key, {}).get("allowed_deposit_modes")
    if allowed_by_type in (None, False):
        return True
    allowed_modes = allowed_by_type.get(campaign_type, set())
    return deposit_mode in allowed_modes

def detect_status(text: str) -> str:
    parts = []
    if "진행중" in text:
        parts.append("진행중")
    if "참여가능" in text:
        parts.append("참여가능")
    return ",".join(parts) if parts else "UNKNOWN"

def load_success_csq() -> set:
    return load_csq_file(SUCCESS_CSQ_FILE)

def save_success_csq(csq: str):
    append_csq_file(SUCCESS_CSQ_FILE, csq, "성공 CSQ 영구 저장")

def load_account_fail_csq() -> set:
    if not PERSIST_ACCOUNT_FAIL or not ACCOUNT_FAIL_CSQ_FILE:
        return set()
    return load_csq_file(ACCOUNT_FAIL_CSQ_FILE)

def save_account_fail_csq(csq: str):
    if not PERSIST_ACCOUNT_FAIL or not ACCOUNT_FAIL_CSQ_FILE:
        return
    append_csq_file(ACCOUNT_FAIL_CSQ_FILE, csq, "계정 체크 실패 CSQ 영구 저장")


def parse_open_dt_from_text(text: str) -> Optional[datetime]:
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
    else:
        m2 = re.search(r"(\d{1,2})\s*시(?:(\d{1,2})\s*분)?", text)
        if m2:
            hh = int(m2.group(1))
            mm = int(m2.group(2)) if m2.group(2) else 0
        else:
            return None

    now = datetime.now()
    open_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if open_dt < now - timedelta(minutes=1):
        open_dt += timedelta(days=1)
    return open_dt


def load_csq_file(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return set()
    return set(x.strip() for x in text.split(",") if x.strip())

def append_csq_file(path: str, csq: str, label: str):
    csq = str(csq).strip()
    if not csq:
        return
    saved = load_csq_file(path)
    if csq in saved:
        return
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", encoding="utf-8") as f:
            f.write(csq)
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write("," + csq)
    log(f"[완료] {label}: {csq}")


_list_cycle_count = 0
_last_popup_close_at = 0.0


def goto_list_page(driver: webdriver.Chrome, force_get: bool = False):
    global _list_cycle_count

    if force_get or (not USE_REFRESH_OVER_GET):
        driver.get(LIST_URL)
        _list_cycle_count = 0
        return

    try:
        if not driver.current_url.startswith(LIST_URL):
            driver.get(LIST_URL)
            _list_cycle_count = 0
            return

        if _list_cycle_count >= LIST_REOPEN_EVERY:
            driver.get(LIST_URL)
            _list_cycle_count = 0
        else:
            driver.refresh()
            _list_cycle_count += 1
    except Exception:
        driver.get(LIST_URL)
        _list_cycle_count = 0


def collect_visible_cards(driver: webdriver.Chrome) -> List[Dict]:
    try:
        return driver.execute_script("""
            return Array.from(document.querySelectorAll('.review_item')).map((card, idx) => {
                const rect = card.getBoundingClientRect();
                // ⭐ 이 사이트는 상품명이 .ctooltip-text(툴팁, 안 잘린 전체 텍스트) 안에 들어있음
                //    (.ctooltip 자체는 화면표시용으로 일부 잘려있을 수 있음)
                const tipEl = card.querySelector('.card-body p .ctooltip-text');
                let title = '';
                if (tipEl) {
                    title = (tipEl.innerText || tipEl.textContent || '').trim();
                } else {
                    const ctEl = card.querySelector('.card-body p .ctooltip');
                    if (ctEl) {
                        // 하위 .ctooltip-text를 제외한 상위 노드의 첫 텍스트만 사용
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
    except Exception:
        return []


def get_card_element_by_index(driver: webdriver.Chrome, card_index: int):
    try:
        cards = driver.find_elements(By.CSS_SELECTOR, ".review_item")
        if 0 <= int(card_index) < len(cards):
            return cards[int(card_index)]
    except Exception:
        pass
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

    if BLOCK_IMAGES:
        prefs["profile.managed_default_content_settings.images"] = 2

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
    options.add_argument("--disable-ipc-flooding-protection")
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
        if BLOCK_IMAGES:
            blocked += ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.ico"]
        if BLOCK_FONTS:
            blocked += ["*.woff", "*.woff2", "*.ttf", "*.otf"]
        if BLOCK_MEDIA:
            blocked += ["*.mp4", "*.webm", "*.mp3", "*.m4a", "*.avi", "*.mov"]
        if blocked:
            driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": blocked})
    except Exception:
        pass

    return driver


# =========================
# 사이트 팝업 처리
# =========================
def close_site_popups(driver: webdriver.Chrome, force: bool = False):
    global _last_popup_close_at

    now_ts = time.time()
    if (not force) and (now_ts - _last_popup_close_at < POPUP_COOLDOWN_SEC):
        return

    _last_popup_close_at = now_ts

    try:
        popups = driver.find_elements(By.CSS_SELECTOR, ".review_popup")
        if popups:
            for p in popups:
                try:
                    if not p.is_displayed():
                        continue
                    try:
                        link_24h = p.find_element(
                            By.XPATH,
                            ".//div[contains(@class,'review_popup_footer')]//a[contains(normalize-space(.),'24시간') and contains(normalize-space(.),'보지 않기')]"
                        )
                        driver.execute_script("arguments[0].click();", link_24h)
                        time.sleep(0.05)
                    except Exception:
                        pass
                    try:
                        if p.is_displayed():
                            link_close = p.find_element(
                                By.XPATH,
                                ".//div[contains(@class,'review_popup_footer')]//a[normalize-space(.)='닫기' or contains(normalize-space(.),'닫기')]"
                            )
                            driver.execute_script("arguments[0].click();", link_close)
                            time.sleep(0.03)
                    except Exception:
                        pass
                except Exception:
                    pass
            try:
                driver.execute_script("document.body.style.overflow='auto';")
            except Exception:
                pass
            return
    except Exception:
        pass

    try:
        driver.execute_script("""
            document.querySelectorAll('.review_popup').forEach(p => p.remove());
            const area = document.querySelector('.review_popup_area');
            if (area) area.remove();
            document.body.style.overflow='auto';
        """)
    except Exception:
        pass


# =========================
# 로그인
# =========================
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
# 모달 표시 판정
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
    except Exception:
        return False

def is_join_accounts_modal_showing(driver: webdriver.Chrome) -> bool:
    return _has_show_class(driver, "join_accounts_modal")

def is_confirm_modal_showing(driver: webdriver.Chrome) -> bool:
    return _has_show_class(driver, "confirm_modal")

def is_alert_modal_showing(driver: webdriver.Chrome) -> bool:
    return _has_show_class(driver, "alert_modal")

def get_alert_msg_text(driver: webdriver.Chrome) -> str:
    try:
        return (driver.find_element(By.ID, "alert_msg").text or "").strip()
    except Exception:
        return ""

def get_join_account_count(driver: webdriver.Chrome) -> int:
    try:
        return int(driver.execute_script("""
            const modal = document.getElementById('join_accounts_modal');
            if (!modal) return 0;
            return modal.querySelectorAll("input.member_store_seq[type='checkbox']").length;
        """) or 0)
    except Exception:
        return 0


# =========================
# JOIN 클릭 이후 이벤트 대기
# =========================
def wait_for_post_join_event(driver: webdriver.Chrome, timeout: float = 2.0) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        pause_point()
        if is_join_accounts_modal_showing(driver):
            return "accounts"
        if is_confirm_modal_showing(driver):
            return "confirm"
        if is_alert_modal_showing(driver) and get_alert_msg_text(driver):
            return "alert"
        time.sleep(0.02)
    return ""


# =========================
# 결과 알림 처리
# =========================
def handle_result_alert(driver: webdriver.Chrome, current_csq: Optional[str], failed_once: set,
                        current_account_index: Optional[int] = None,
                        available_account_count: int = 0,
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
                if msg:
                    break
        except Exception:
            pass
        time.sleep(0.02)

    if not msg:
        log(f"[안내] alert 결과 없음 (대기 타임아웃) → {current_csq}{name_tag}")
        return "none"

    log(f"[결과] 결과 메시지: {msg} (csq={current_csq}{name_tag})")
    _log_tuning_summary(msg)
    save_current_page_html(driver, f"alert_{current_csq or 'unknown'}")

    result = "fail"

    if "참여하실 계정을 선택해 주세요" in msg:
        result = "none"
    elif current_csq and "캠페인에 참여 되었습니다" in msg:
        save_success_csq(current_csq)
        log(f"[완료] 참여 성공 → 영구 저장 완료: {current_csq}")
        result = "success"
    elif current_csq and ("이미 참여한 캠페인입니다" in msg or "이미 참여한 계정입니다" in msg):
        # 이 두 메시지는 "내가(내 계정이) 이 캠페인에 실제로 성공했다"는 의미라 영구저장 대상
        save_success_csq(current_csq)
        log(f"[완료] 이미 참여(최종 처리) → 영구 저장 완료: {current_csq}")
        result = "success"
    elif current_csq and "참여 할 수 있는 횟수를 초과하여" in msg:
        # ⭐ 이 메시지는 "이 캠페인을 성공적으로 땄다"는 뜻이 아니라 계정 차원의 참여 횟수 제한일 수 있어서
        #    영구저장(success_csq)하면 안 됨 — 잘못 저장하면 실제로 못 딴 캠페인을 영원히 스킵하게 됨.
        if current_account_index == 0 and available_account_count >= 2:
            log("[재시도] 첫번째 계정은 횟수초과 → 두번째 계정 1회 재시도")
            result = "retry_second"
        else:
            failed_once.add(current_csq)
            log(f"[경고] 참여 횟수 초과(영구저장 안 함) → 이번 실행에서만 스킵: {current_csq}{name_tag}")
            result = "fail"
    elif current_csq and "참여 가능한 시간이 아닙니다" in msg:
        log(f"[대기] 아직 오픈 시간이 아님(서버 알림) → 블랙리스트 제외 후 계속 감시: {current_csq}")
        log("[경고] 너무 일찍 도착했어요 — click_tuning.txt의 lead_ms를 낮춰보세요")
        result = "fail"
    elif current_csq and ("참여 요청이 많아" in msg or "다시 참여" in msg):
        # ⭐ 서버가 명시적으로 재시도를 권하는 혼잡 메시지 → 실패 처리 대신 즉시 재시도하도록 신호
        log(f"[재시도] 서버 혼잡 메시지 감지 → 즉시 재시도: {current_csq}{name_tag}")
        result = "retry_congestion"
    else:
        if current_csq:
            failed_once.add(current_csq)
            log(f"[경고] 성공 아님 → 이번 실행에서만 스킵: {current_csq}")
        result = "fail"

    try:
        btn = driver.find_element(By.CSS_SELECTOR, "#alert_modal .btn-warning")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.05)
    except Exception:
        pass

    return result


# =========================
# 계정 선택 모달 처리
# =========================
def handle_account_modal(driver: webdriver.Chrome, current_csq: Optional[str] = None, account_index: int = 0) -> Tuple[bool, int]:
    t0 = time.time()
    while time.time() - t0 < 2.0:
        if is_join_accounts_modal_showing(driver):
            log(f"[완료] 계정선택창 표시 감지 (+{int((time.time()-t0)*1000)}ms)")
            break
        time.sleep(0.02)
    else:
        return False, 0

    detected_count = 0

    for attempt in range(1, 4):
        try:
            t_wait_cb = time.time()
            count = 0
            while time.time() - t_wait_cb < 2.0:
                count = driver.execute_script("""
                    const modal = document.getElementById('join_accounts_modal');
                    if (!modal) return 0;
                    return modal.querySelectorAll("input.member_store_seq[type='checkbox']").length;
                """)
                if count:
                    break
                time.sleep(0.02)

            if count:
                detected_count = count

            if not count:
                log(f"[경고] 계정 목록 없음 (attempt {attempt})")
                time.sleep(0.08)
                continue

            if account_index >= count:
                log(f"[경고] 요청한 계정 index={account_index} 없음 (총 {count}개)")
                if current_csq:
                    save_account_fail_csq(current_csq)
                return False, detected_count

            t_chk0 = time.time()
            ok = driver.execute_script("""
                const modal = document.getElementById('join_accounts_modal');
                if (!modal) return false;
                const cbs = Array.from(modal.querySelectorAll("input.member_store_seq[type='checkbox']"));
                if (!cbs.length) return false;
                cbs.forEach(cb => {
                    cb.checked = false;
                    cb.removeAttribute('checked');
                    cb.dispatchEvent(new Event('input',  {bubbles:true}));
                    cb.dispatchEvent(new Event('change', {bubbles:true}));
                });
                const cb = cbs[arguments[0]];
                if (!cb) return false;
                cb.click();
                cb.checked = true;
                cb.setAttribute('checked','checked');
                cb.dispatchEvent(new Event('input',  {bubbles:true}));
                cb.dispatchEvent(new Event('change', {bubbles:true}));
                return cb.checked === true;
            """, account_index)
            if not ok:
                log(f"[경고] 계정 체크 실패 (attempt {attempt}, index={account_index})")
                time.sleep(0.08)
                continue
            log(f"[완료] 계정 체크 완료 index={account_index} total={count} (+{int((time.time()-t_chk0)*1000)}ms)")

            t_call0 = time.time()
            called = driver.execute_script("""
                try {
                    if (typeof join_campaign_confirm === 'function') {
                        join_campaign_confirm();
                        if (window.jQuery) { $('#join_accounts_modal').modal('hide'); }
                        return true;
                    }
                    const btn = document.getElementById('join_campaign_confirm');
                    if (btn) { btn.click(); if (window.jQuery) { $('#join_accounts_modal').modal('hide'); } return true; }
                    return false;
                } catch(e) { return false; }
            """)
            if not called:
                log(f"[경고] 참여하기 호출 실패 (attempt {attempt}, index={account_index})")
                time.sleep(0.10)
                continue
            log(f"[완료] 참여하기 호출 완료 index={account_index} (+{int((time.time()-t_call0)*1000)}ms)")

            t_evt = time.time()
            while time.time() - t_evt < 4.0:
                if is_confirm_modal_showing(driver):
                    log(f"[완료] 계정 제출 후 참여확인창 감지 (+{int((time.time()-t_evt)*1000)}ms)")
                    return True, detected_count
                if is_alert_modal_showing(driver) and get_alert_msg_text(driver):
                    log(f"[완료] 계정 제출 후 alert 감지 (+{int((time.time()-t_evt)*1000)}ms)")
                    return True, detected_count
                if not is_join_accounts_modal_showing(driver):
                    log(f"[완료] 계정선택창 닫힘 확인 (+{int((time.time()-t_evt)*1000)}ms)")
                    return True, detected_count
                time.sleep(0.03)

            log(f"[경고] 제출 후 이벤트 대기 실패 (attempt {attempt}, index={account_index})")
            time.sleep(0.12)

        except Exception as e:
            log(f"[경고] handle_account_modal 오류 (attempt {attempt}, index={account_index}): {e}")
            time.sleep(0.12)

    if current_csq:
        save_account_fail_csq(current_csq)
        log(f"[복귀] 계정 체크 3회 실패 → 자동 감시 복귀: {current_csq}")
    return False, detected_count


# =========================
# confirm 모달 처리
# =========================
_last_confirm_at = 0.0

def handle_confirm_modal(driver: webdriver.Chrome) -> bool:
    return click_confirm_modal(driver)


# =========================
# 참여 버튼 클릭 (상세)
# =========================
def try_click_join_button_in_detail_fast(driver: webdriver.Chrome, csq: str) -> str:
    if is_join_accounts_modal_showing(driver) or is_confirm_modal_showing(driver) or is_alert_modal_showing(driver):
        return "CLICKED"

    script = r"""
        const csq = String(arguments[0]);
        let targetBtn = document.getElementById('join_campaign_btn');
        let btnText = '';
        
        if (targetBtn && (targetBtn.offsetParent !== null || getComputedStyle(targetBtn).display !== 'none')) {
            btnText = (targetBtn.innerText || targetBtn.textContent || '').replace(/\s+/g, ' ').trim();
        } else {
            targetBtn = null;
            const candidates = Array.from(document.querySelectorAll('button, a, div'));
            for (const el of candidates) {
                const oc = el.getAttribute('onclick') || '';
                const dataCsq = el.getAttribute('data-csq') || '';
                if ((oc.includes('join_campaign_pop') && oc.includes(csq)) || dataCsq === csq) {
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || el.offsetParent === null) continue;
                    
                    targetBtn = el;
                    btnText = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                    break;
                }
            }
        }
        
        if (!targetBtn) {
            return 'NOT_FOUND';
        }
        
        if (btnText) {
            if (btnText.includes('마감') || btnText.includes('종료') || btnText.includes('불가')) {
                return 'CLOSED:' + btnText;
            }
            if (/\d{1,2}:\d{2}/.test(btnText) || /\d{1,2}시/.test(btnText)) {
                if (!btnText.includes('참여')) {
                    return 'WAIT:' + btnText;
                }
            }
        }
        
        try {
            if (typeof join_campaign_pop === 'function') {
                join_campaign_pop(csq);
                return 'CLICKED';
            }
        } catch (e) {}
        
        targetBtn.click();
        return 'CLICKED';
    """
    try:
        res = driver.execute_script(script, str(csq))
        return str(res) if res else "NOT_FOUND"
    except Exception:
        return "NOT_FOUND"


def fast_enter_detail_and_click_join(driver: webdriver.Chrome, card_el, csq: str,
                                     timeout: float = 0.6, popup_retry_after: float = 0.12) -> str:
    t0 = time.time()
    popup_cleaned = False
    driver.execute_script("arguments[0].click();", card_el)

    while time.time() - t0 < timeout:
        pause_point()
        if is_join_accounts_modal_showing(driver) or is_confirm_modal_showing(driver) or is_alert_modal_showing(driver):
            return "CLICKED"

        res = try_click_join_button_in_detail_fast(driver, csq)
        if res == "CLICKED":
            return "CLICKED"
        elif res.startswith("CLOSED") or res.startswith("WAIT"):
            return res

        elapsed = time.time() - t0
        if (not popup_cleaned) and elapsed >= popup_retry_after:
            try:
                close_site_popups(driver)
            except Exception:
                pass
            popup_cleaned = True
            
            res = try_click_join_button_in_detail_fast(driver, csq)
            if res == "CLICKED":
                return "CLICKED"
            elif res.startswith("CLOSED") or res.startswith("WAIT"):
                return res

        time.sleep(0.01)

    return "NOT_FOUND"


def try_click_join_button_in_detail(driver: webdriver.Chrome, csq: str) -> bool:
    return try_click_join_button_in_detail_fast(driver, csq) == "CLICKED"


def try_open_join_without_button_search(driver: webdriver.Chrome, csq: str) -> bool:
    if is_join_accounts_modal_showing(driver) or is_confirm_modal_showing(driver) or is_alert_modal_showing(driver):
        return False

    script = r"""
        const csq = String(arguments[0]);
        try {
            if (typeof join_campaign_pop === 'function') {
                join_campaign_pop(csq);
                return true;
            }
            const candidates = Array.from(document.querySelectorAll('button[onclick], a[onclick]'));
            for (const el of candidates) {
                const oc = el.getAttribute('onclick') || '';
                const txt = (el.innerText || el.textContent || '').trim();
                if (oc.includes('join_campaign_pop') && (oc.includes(csq) || txt.includes('캠페인 참여'))) {
                    el.click();
                    return true;
                }
            }
        } catch (e) {}
        return false;
    """
    try:
        return bool(driver.execute_script(script, str(csq)))
    except Exception:
        return False


def process_immediate_join_flow(driver: webdriver.Chrome, csq: str, failed_once: set, campaign_name: str = "") -> None:
    name_tag = f" [{campaign_name}]" if campaign_name else ""
    log(f"[진행] JOIN 버튼 클릭 감지(즉시 참여): csq={csq}{name_tag}")
    save_current_page_html(driver, f"detail_{csq}_after_join_click")

    kind = wait_for_post_join_event(driver, timeout=2.0)
    log(f"[감지] JOIN 후 이벤트 감지(즉시 참여): {kind or 'none'} (csq={csq})")

    account_try_index = 0
    account_count_hint = 0

    if kind == "accounts" or is_join_accounts_modal_showing(driver):
        handled, detected_count = handle_account_modal(driver, csq, account_index=account_try_index)
        account_count_hint = max(account_count_hint, detected_count)
        log(f"[완료] 계정선택창 처리결과(즉시 참여): {handled} (csq={csq}, index={account_try_index}, total={account_count_hint})")
        if not handled:
            failed_once.add(csq)
            log(f"[경고] 계정 체크 3회 실패(즉시 참여) → 이번 실행 스킵 후 목록 복귀: {csq}")
            driver.get(LIST_URL)
            time.sleep(0.35)
            close_site_popups(driver, force=True)
            return

    if kind == "confirm" or is_confirm_modal_showing(driver):
        handle_confirm_modal(driver)

    alert_result = handle_result_alert(
        driver, csq, failed_once,
        current_account_index=account_try_index,
        available_account_count=account_count_hint,
        campaign_name=campaign_name
    )

    if alert_result == "retry_second":
        log(f"[재시도] 즉시 참여: 두번째 계정 재시도 시작 (csq={csq}{name_tag})")

        res_fast = try_click_join_button_in_detail_fast(driver, csq)
        if res_fast == "CLICKED" or try_open_join_without_button_search(driver, csq):
            kind2 = wait_for_post_join_event(driver, timeout=2.0)
            log(f"[감지] 두번째 계정용 JOIN 후 이벤트 감지: {kind2 or 'none'} (csq={csq})")

            if kind2 == "accounts" or is_join_accounts_modal_showing(driver):
                account_try_index = 1
                handled2, detected_count2 = handle_account_modal(driver, csq, account_index=account_try_index)
                account_count_hint = max(account_count_hint, detected_count2)
                log(f"[완료] 계정선택창 처리결과(즉시 참여/2차): {handled2} (csq={csq}, index={account_try_index}, total={account_count_hint})")
                if not handled2:
                    failed_once.add(csq)

            if is_confirm_modal_showing(driver):
                handle_confirm_modal(driver)

            handle_result_alert(
                driver, csq, failed_once,
                current_account_index=account_try_index,
                available_account_count=account_count_hint,
                campaign_name=campaign_name
            )
    elif alert_result == "retry_congestion":
        for retry_i in range(1, CONGESTION_RETRY_MAX + 1):
            log(f"[재시도] 즉시 참여 혼잡 재시도 {retry_i}/{CONGESTION_RETRY_MAX}: {csq}{name_tag}")
            time.sleep(CONGESTION_RETRY_DELAY_SEC)
            if is_confirm_modal_showing(driver):
                handle_confirm_modal(driver)
            else:
                res_fast = try_click_join_button_in_detail_fast(driver, csq)
                if res_fast != "CLICKED":
                    try_open_join_without_button_search(driver, csq)
            retry_result = handle_result_alert(
                driver, csq, failed_once,
                current_account_index=account_try_index,
                available_account_count=account_count_hint,
                campaign_name=campaign_name
            )
            if retry_result != "retry_congestion":
                break
        else:
            log(f"[경고] 즉시 참여 혼잡 재시도 {CONGESTION_RETRY_MAX}회 초과 → 포기: {csq}{name_tag}")
            failed_once.add(csq)

    driver.get(LIST_URL)
    time.sleep(0.35)
    close_site_popups(driver)


# =========================
# 상세 선점 대기 및 서버 시간 맞춤
# =========================
def build_requests_session_from_driver(driver: webdriver.Chrome) -> requests.Session:
    s = requests.Session()
    try:
        ua = driver.execute_script("return navigator.userAgent")
    except Exception:
        ua = "Mozilla/5.0"

    s.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": driver.current_url or LIST_URL,
    })

    try:
        for c in driver.get_cookies():
            s.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain"), path=c.get("path", "/"))
    except Exception:
        pass
    return s


def parse_http_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def fast_fetch_detail_html(driver: webdriver.Chrome, session: requests.Session, url: str) -> Tuple[str, Optional[datetime], float, float]:
    try:
        for c in driver.get_cookies():
            session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain"), path=c.get("path", "/"))
    except Exception:
        pass

    t0 = time.time()
    r = session.get(url, timeout=REQUEST_TIMEOUT_SEC)
    t1 = time.time()
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    try:
        save_html(f"detail_requests_{os.path.basename(url).split('?')[0] or 'raw'}", r.text or "")
    except Exception:
        pass
    server_dt = parse_http_date(r.headers.get("Date"))
    return r.text, server_dt, t0, t1


# ── 정밀 시계 (네이버 서버 시각, 밀리초 단위) ────────────────────────────────
# 예전에는 서버 Date 헤더(초 단위로 잘려서 옴)로 오프셋을 재서 최대 ~1초 오차가 생겼음.
# → 밀리초까지 주는 네이버 시각을 여러 번 재서 왕복이 가장 짧은 값을 쓰고,
#   이후에는 PC 시계 대신 단조 증가 타이머(perf_counter)로 시간을 센다.
_CLICK_TIMING_CTX: Optional[Dict] = None   # 정각 대기가 끝난 직후 첫 클릭에 넘겨주는 정보(1회용)

_NAVER_SEARCH_URL = "https://search.naver.com/search.naver?query=%EB%84%A4%EC%9D%B4%EB%B2%84%EC%8B%9C%EA%B3%84"
_NAVER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"


class NaverClock:
    def __init__(self):
        self.Ready = False
        self.RoundTripMs = 0.0
        self.key = None
        self.anchor = None
        self.anchorTicks = 0.0
        # 연결 재사용(keep-alive): 샘플마다 새로 연결하면 왕복에 연결 맺는 시간이 섞여 시계가 앞서가 버림
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": _NAVER_UA, "Referer": _NAVER_SEARCH_URL})

    @property
    def Age(self) -> float:
        if not self.Ready:
            return float("inf")
        return time.perf_counter() - self.anchorTicks

    @property
    def Fresh(self) -> bool:
        return self.Ready and self.Age < 120.0

    @property
    def Now(self) -> datetime:
        if not self.Ready:
            raise RuntimeError("네이버 시각 미동기화")
        return self.anchor + timedelta(seconds=(time.perf_counter() - self.anchorTicks))

    def Apply(self, sample: dict):
        # 서버가 시각을 찍은 뒤 응답이 도착하기까지 걸린 시간 ≈ 왕복의 절반
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
        m = re.search(
            r'"sApiNowTime"\s*:\s*"https://m\.search\.naver\.com/p/csearch/dcontent/util/time\.naver"'
            r'[\s\S]{0,500}?"passportKey"\s*:\s*"([A-Za-z0-9_-]+)"', html)
        if not m:
            raise ValueError("네이버 시계 연결 방식이 변경됐습니다.")
        return m.group(1)

    @staticmethod
    def ParseTime(text: str) -> datetime:
        m = re.search(r'oneClickClock\(\s*"(\d{4}/\d{2}/\d{2}/\d{2}/\d{2}/\d{2}/\d{3})"\s*\)\s*;?$', text.strip())
        if not m:
            raise ValueError("네이버 시각 응답을 읽지 못했습니다.")
        p = m.group(1).split("/")
        return datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5]), int(p[6]) * 1000)

    def _request_sample(self) -> dict:
        url = ("https://m.search.naver.com/p/csearch/dcontent/util/time.naver?passportKey="
               + urllib.parse.quote(self.key)
               + "&_format=yyyy%2FMM%2Fdd%2FHH%2Fmm%2Fss%2FSSS&site=&_callback=oneClickClock&_=" + uuid.uuid4().hex)
        t0 = time.perf_counter()
        text = self._get(url)
        t1 = time.perf_counter()
        rtt = (t1 - t0) * 1000.0
        if rtt > 2000.0:
            raise ValueError("통신 지연이 커서 시각을 맞추지 못했습니다.")
        return {"Server": self.ParseTime(text), "Received": t1, "RoundTripMs": rtt}

    def Fetch(self) -> dict:
        had_key = self.key is not None
        if self.key is None:
            self.key = self.ParseKey(self._get(_NAVER_SEARCH_URL))

        last_err = None
        # 첫 요청은 연결(TCP/TLS)을 맺느라 왕복이 부풀기 때문에 버리고, 그다음 3번을 잰다.
        try:
            self._request_sample()
        except Exception as e:
            last_err = e
            if had_key:   # 키가 만료됐을 수 있어 한 번 갱신
                try:
                    self.key = self.ParseKey(self._get(_NAVER_SEARCH_URL))
                except Exception:
                    pass

        best = None
        for i in range(3):
            try:
                smp = self._request_sample()
                if best is None or smp["RoundTripMs"] < best["RoundTripMs"]:
                    best = smp
            except Exception as e:
                last_err = e
            if i < 2:
                time.sleep(0.05)
        if best is None:
            raise RuntimeError(f"네이버 시각을 가져오지 못했습니다: {last_err}")
        return best


_naver_clock = NaverClock()


def _sync_naver_clock(reason: str) -> bool:
    """네이버 시각으로 정밀 시계를 맞춘다. 실패해도 예외를 밖으로 던지지 않음(→ PC 시계로 계속 진행)."""
    t_start = time.perf_counter()
    try:
        _naver_clock.Apply(_naver_clock.Fetch())
    except Exception as e:
        log(f"[경고] 네이버 시각 동기화 실패({reason}) → 이번엔 PC 시계로 진행: {e}")
        return False
    spent = int((time.perf_counter() - t_start) * 1000)
    pc_minus_naver = (datetime.now() - _naver_clock.Now).total_seconds()
    log(f"[대기] 네이버 시각 동기화({reason}): 왕복 {_naver_clock.RoundTripMs:.0f}ms, "
        f"PC시계-네이버 {pc_minus_naver:+.3f}s, 소요 {spent}ms")
    return True


def _precise_now() -> datetime:
    """정밀 시계(네이버)가 준비돼 있으면 그 시각, 아니면 PC 시계."""
    if _naver_clock.Ready:
        return _naver_clock.Now
    return datetime.now()


# ── 클릭 튜닝 설정 파일 (click_tuning.txt) ─────────────────────────────────────
# 메모장으로 고치고 저장하면 다음 참여부터 바로 적용 (프로그램 재시작/GitHub 업로드 불필요).
# 폴더(인스턴스)마다 따로 둘 수 있어서, PC/계정별로 값을 다르게 두고 같은 캠페인에서 비교해볼 수 있음.
_TUNING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "click_tuning.txt")
_TUNING_DEFAULT_TEXT = """# 정각 클릭 튜닝 — 메모장으로 고치고 저장하면 다음 참여부터 바로 적용됩니다 (재시작 불필요)
#
# lead_ms : 클릭을 정각보다 몇 ms 먼저 보낼지. 0 = 정각에 딱 맞춤, 숫자가 클수록 더 일찍 보냅니다.
#           결과가 "너무 일찍 도착"으로 나오면 값을 낮추세요. (로그의 [튜닝] 줄 참고)
# inpage  : 1 = 브라우저 안에서 정각에 직접 클릭 (파이썬→브라우저 전달 지연이 없어짐. 실전 검증 전)
#           0 = 파이썬이 정각에 클릭 명령을 보냄 (기존 방식, 검증됨)
# warmup  : 1 = 정각 2~3초 전에 사이트와의 연결을 한 번 확인해 둠 (연결이 식어서 첫 요청이 느려지는 것 방지)
lead_ms = 0
inpage = 0
warmup = 1
"""
_LAST_TIMED_CLICK: Optional[Dict] = None   # 마지막 정각 클릭 기록 ([튜닝] 요약 줄용, 1회용)


def _read_tuning() -> Dict:
    """click_tuning.txt를 읽어 {lead_ms, inpage, warmup, warn} 반환. 없으면 기본값으로 파일을 만들어 둠."""
    cfg = {"lead_ms": float(CONFIRM_CLICK_LEAD_SEC) * 1000.0, "inpage": False, "warmup": True, "warn": ""}
    try:
        if not os.path.exists(_TUNING_FILE):
            try:
                with open(_TUNING_FILE, "w", encoding="utf-8") as f:
                    f.write(_TUNING_DEFAULT_TEXT)
            except Exception:
                pass
            return cfg
        with open(_TUNING_FILE, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except Exception as e:
        cfg["warn"] = f"click_tuning.txt를 읽지 못해 기본값 사용: {e}"
        return cfg

    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, val = [x.strip() for x in line.split("=", 1)]
        key = key.lower()
        try:
            if key == "lead_ms":
                v = float(val)
                cfg["lead_ms"] = max(-100.0, min(500.0, v))
                if v != cfg["lead_ms"]:
                    cfg["warn"] += f"lead_ms={v:g}는 범위(-100~500)를 벗어나 {cfg['lead_ms']:g}로 보정됨. "
            elif key == "inpage":
                cfg["inpage"] = val in ("1", "true", "on", "yes")
            elif key == "warmup":
                cfg["warmup"] = val in ("1", "true", "on", "yes")
        except ValueError:
            cfg["warn"] += f"'{raw.strip()}' 줄을 읽지 못해 무시함. "
    return cfg


def _naive_epoch_ms(dt: datetime) -> float:
    return (dt - datetime(1970, 1, 1)).total_seconds() * 1000.0


# ── 연결 예열: 정각 2~3초 전에 같은 사이트로 작은 요청 2번 (결과는 클릭 뒤에 읽음 → 대기를 막지 않음) ──
_WARM_JS = r"""
window.__gt_warm = {done: false};
(async () => {
    const w = window.__gt_warm;
    const one = async () => {
        const ac = new AbortController();
        const to = setTimeout(() => ac.abort(), 900);
        const t0 = performance.now();
        try {
            const r = await fetch(location.origin + '/', {method: 'HEAD', cache: 'no-store',
                                                           credentials: 'same-origin', signal: ac.signal});
            clearTimeout(to);
            return [Math.round((performance.now() - t0) * 10) / 10, r.status];
        } catch (e) {
            clearTimeout(to);
            return [Math.round((performance.now() - t0) * 10) / 10, 'err'];
        }
    };
    w.a = await one();
    w.b = await one();
    w.done = true;
})();
return true;
"""


def _warm_connection(driver) -> None:
    try:
        driver.execute_script(_WARM_JS)
    except Exception:
        pass


def _read_warm_result(driver) -> str:
    try:
        w = driver.execute_script("return window.__gt_warm || null;")
    except Exception:
        return ""
    if not w or not w.get("done"):
        return ""
    a, b = w.get("a") or [0, "?"], w.get("b") or [0, "?"]
    hint = ""
    try:
        if float(a[0]) > float(b[0]) * 1.8 and float(a[0]) - float(b[0]) > 15:
            hint = " → 첫 요청이 눈에 띄게 느림(연결이 식어 있었음, 예열이 효과 봄)"
        else:
            hint = " → 두 요청 속도가 비슷(연결은 이미 따뜻했음)"
    except Exception:
        pass
    return f"첫 요청 {a[0]}ms({a[1]}) / 두 번째 {b[0]}ms({b[1]}){hint}"


# ── 브라우저 안에서 정각에 직접 클릭 (inpage=1) ─────────────────────────────────────
# 파이썬이 크롬에 명령을 보내는 지연(2~4ms, PC가 바쁘면 더 크게 흔들림)을 없애기 위해,
# 브라우저 시계로 환산한 발사 시각을 페이지에 심어두고 페이지가 직접 그 시각에 누른다.
_JS_CLICK_FN = r"""
function __gt_click(nowFn) {
    try {
        const el = document.getElementById('confirm_modal');
        if (!el) return ['no_modal', nowFn()];
        const cls = (el.getAttribute('class') || '');
        const st  = (el.getAttribute('style') || '');
        const show = cls.split(/\s+/).includes('show');
        const disp_block = st.includes('display: block');
        const visible = !!(show || disp_block) && (el.offsetParent !== null || disp_block);
        if (!visible) return ['not_shown', nowFn()];
        const t = nowFn();   // 참여확인 함수를 실제로 부르는 순간
        if (typeof confirm_func === 'function') {
            confirm_func();
            if (window.jQuery) { $('#confirm_modal').modal('hide'); }
            return ['clicked', t];
        }
        const btn = document.querySelector("#confirm_modal button.butn.butn-yellow");
        if (btn) { btn.click(); return ['clicked', t]; }
        return ['no_handler', t];
    } catch (e) { return ['error', nowFn()]; }
}
"""
_CONFIRM_CLICK_JS = _JS_CLICK_FN + "\nreturn __gt_click(() => Date.now());"

_INPAGE_ARM_JS = _JS_CLICK_FN + r"""
const fireAt = arguments[0];
if (window.__gt_timer) { try { clearTimeout(window.__gt_timer); } catch (e) {} }
window.__gt = {fired: false, status: null, t: null};
const perfNow = () => performance.timeOrigin + performance.now();
function fire() {
    const g = window.__gt;
    if (!g || g.fired) return;
    g.fired = true;                       // 먼저 표시 → 파이썬 쪽 보조 클릭과 중복 방지
    const r = __gt_click(perfNow);
    g.status = r[0]; g.t = r[1];
}
function tick() {
    const rem = fireAt - perfNow();
    if (rem <= 0) { fire(); return; }
    if (rem > 30) { window.__gt_timer = setTimeout(tick, Math.max(1, rem - 25)); return; }
    while (perfNow() < fireAt) {}         // 마지막 ~30ms는 타이머 대신 계속 확인
    fire();
}
tick();
return true;
"""

_INPAGE_FALLBACK_JS = _JS_CLICK_FN + r"""
if (window.__gt && window.__gt.fired) return ['already', null];
if (window.__gt) { window.__gt.fired = true; }
return __gt_click(() => performance.timeOrigin + performance.now());
"""

_INPAGE_DISARM_JS = r"""
if (window.__gt) { window.__gt.fired = true; }
if (window.__gt_timer) { try { clearTimeout(window.__gt_timer); } catch (e) {} }
return true;
"""


def _sync_browser_clock(driver, samples: int = 6):
    """브라우저 시계와 정밀 시계의 차이(ms)를 잰다. 왕복이 가장 짧은 샘플 사용 → 오차 ≈ ±왕복/2."""
    best = None
    for _ in range(samples):
        a = time.perf_counter()
        na = _precise_now()
        js = driver.execute_script("return performance.timeOrigin + performance.now();")
        nb = _precise_now()
        b = time.perf_counter()
        rtt = b - a
        mid = _naive_epoch_ms(na) + (_naive_epoch_ms(nb) - _naive_epoch_ms(na)) / 2.0
        off = float(js) - mid
        if best is None or rtt < best[1]:
            best = (off, rtt)
    return best


def _arm_inpage_click(driver, target: datetime, lead_s: float) -> Optional[Dict]:
    if STOP_SIGNAL.is_set() or PAUSED.is_set():
        return None
    try:
        off_ms, rtt_s = _sync_browser_clock(driver)
        fire_at = _naive_epoch_ms(target) - lead_s * 1000.0 + off_ms
        driver.execute_script(_INPAGE_ARM_JS, fire_at)
        return {"offset_ms": off_ms, "rtt_ms": rtt_s * 1000.0, "fire_at": fire_at}
    except Exception as e:
        log(f"[경고] 브라우저 내부 예약 실패 → 파이썬 방식으로 진행: {e}")
        return None


def _disarm_inpage_click(driver) -> None:
    try:
        driver.execute_script(_INPAGE_DISARM_JS)
    except Exception:
        pass


def _collect_inpage_result(driver, deadline_s: float = 0.35):
    """예약된 클릭 결과를 받아온다. 제때 안 눌렸으면 파이썬이 대신 누름(중복 클릭은 페이지 안 플래그로 방지).
    반환: (status, 브라우저시각(ms, epoch) 또는 None, 경로 'inpage'/'fallback'/'none')"""
    def poll():
        try:
            return driver.execute_script("const g = window.__gt; return (g && g.status) ? [g.status, g.t] : null;")
        except Exception:
            return None

    t_end = time.perf_counter() + deadline_s
    while time.perf_counter() < t_end:
        r = poll()
        if r:
            return r[0], r[1], "inpage"
        time.sleep(0.003)

    try:
        r = driver.execute_script(_INPAGE_FALLBACK_JS)
    except Exception:
        r = None
    if r and r[0] != "already":
        return r[0], r[1], "fallback"

    t_end = time.perf_counter() + 0.5       # 그 사이 예약 타이머가 눌렀을 수 있으니 결과를 조금 더 기다림
    while time.perf_counter() < t_end:
        r = poll()
        if r:
            return r[0], r[1], "inpage"
        time.sleep(0.005)
    return "error", None, "none"


def _log_tuning_summary(msg: str) -> None:
    """정각 클릭 직후 첫 결과 메시지에 대해 한 줄 요약을 남긴다 (여러 번 모아서 lead 조정에 쓰기 위함)."""
    global _LAST_TIMED_CLICK
    rec = _LAST_TIMED_CLICK
    if not rec:
        return
    _LAST_TIMED_CLICK = None
    if time.time() - rec.get("t", 0) > 15.0:      # 오래된 기록이 엉뚱한 결과에 붙지 않도록
        return
    if "참여 가능한 시간이 아닙니다" in msg:
        label = "너무 일찍 도착 (서버: 아직 오픈 전) → lead를 낮추세요"
    elif "참여 요청이 많아" in msg:
        label = "혼잡 (서버가 거절)"
    elif "캠페인에 참여 되었습니다" in msg:
        label = "성공"
    elif "이미 참여" in msg:
        label = "이미 참여"
    elif "마감" in msg:
        label = "마감"
    else:
        label = "기타"
    log(f"[튜닝] lead {rec['lead_ms']:g}ms | 클릭 오픈대비 {rec['after_open_s']:+.3f}s ({rec['path']}) | 결과: {label}")


def wait_until_server_second(driver: webdriver.Chrome, session: requests.Session, url: str, target_open_dt_local: datetime) -> bool:
    """참여확인 클릭을 보낼 시각(오픈 시각 - lead, 네이버 시각 기준) 직전까지 기다린다.
    (session, url 인자는 예전 호환용이라 쓰지 않음)

    - 시계가 2분 안에 맞춰져 있으면 재사용 → 재시도 때 동기화로 시간을 낭비하지 않음
    - 목표가 이미 임박/경과했으면 동기화를 건너뛰고 바로 진행
    - 대기가 길면 60초마다 재동기화 (정각 12초 전부터는 안 함)
    - 정각 2~3초 전: 사이트와 연결 예열(warmup=1), 정각 0.6초 전: 브라우저 내부 예약(inpage=1)
    - 마지막 20ms는 sleep 없이 계속 확인해서 정밀도 확보
    - 대기가 끝난 직후엔 로그를 찍지 않고(클릭이 늦어지므로) 정보만 넘겨서 클릭 뒤에 출력
    """
    global _CLICK_TIMING_CTX
    tun = _read_tuning()
    lead = tun["lead_ms"] / 1000.0
    note = ""

    remain0 = (target_open_dt_local - _precise_now()).total_seconds()
    if remain0 > 1.5:
        if tun["warn"]:
            log(f"[경고] click_tuning.txt: {tun['warn']}")
        log(f"[설정] 클릭 lead {tun['lead_ms']:g}ms | 브라우저 내부 예약 {'켜짐' if tun['inpage'] else '꺼짐'} "
            f"| 연결 예열 {'켜짐' if tun['warmup'] else '꺼짐'}  (click_tuning.txt)")

    last_sync_try = time.perf_counter()
    if _naver_clock.Fresh:
        last_sync_try -= _naver_clock.Age
    elif remain0 <= 1.5:
        note = f"목표 임박/경과({remain0:.2f}s)라 시계 동기화 생략"
    else:
        _sync_naver_clock("최초")
        last_sync_try = time.perf_counter()

    warmed = False
    armed_tried = False
    arm_info = None
    try:
        while True:
            pause_point()
            remain = (target_open_dt_local - _precise_now()).total_seconds() - lead
            if remain <= 0:
                break

            if remain > 12.0 and (time.perf_counter() - last_sync_try) >= 60.0:
                last_sync_try = time.perf_counter()
                _sync_naver_clock("재동기화")
                continue

            if tun["warmup"] and driver is not None and not warmed and 1.2 < remain <= 3.0:
                warmed = True
                _warm_connection(driver)

            if tun["inpage"] and driver is not None and not armed_tried and 0.15 < remain <= 0.6:
                armed_tried = True
                arm_info = _arm_inpage_click(driver, target_open_dt_local, lead)
                continue

            if remain > 500.0:
                time.sleep(5.0)       # 많이 남았을 땐 5초 간격 (정지 반응성 + 리소스 절약)
            elif remain > 6.0:
                time.sleep(1.0)
            elif remain > 1.0:
                time.sleep(0.1)
            elif remain > 0.1:
                time.sleep(0.01)
            elif remain > 0.02:
                time.sleep(0.001)
            # else: 마지막 20ms는 sleep 없이 계속 확인
    except BaseException:
        # 정지/오류로 빠져나가면 브라우저에 심어둔 예약 클릭이 혼자 눌리지 않도록 해제
        if arm_info is not None and driver is not None:
            _disarm_inpage_click(driver)
        raise

    _CLICK_TIMING_CTX = {
        "target": target_open_dt_local,
        "reached_dt": _precise_now(),
        "reached_precise": _naver_clock.Ready,
        "lead": lead,
        "lead_ms": tun["lead_ms"],
        "note": note,
        "inpage": arm_info,
        "warmed": warmed,
    }
    return True


def _log_timed_click(ctx: Dict, disp_dt: Optional[datetime], path_label: str, extra: str, exec_ms: int):
    """정각 대기 직후 클릭의 시각 정보를 로그로 남긴다 (클릭이 끝난 뒤에 호출)."""
    global _LAST_TIMED_CLICK
    target = ctx["target"]
    src = "네이버" if ctx.get("reached_precise") else "PC시계"
    reached = ctx["reached_dt"]
    log(f"[대기] 목표시각 도달: {src} {reached.strftime('%H:%M:%S.%f')[:-3]} "
        f"(오픈 대비 {(reached - target).total_seconds():+.3f}s, lead={ctx.get('lead_ms', 0.0):g}ms)"
        + (f" ※ {ctx['note']}" if ctx.get("note") else ""))

    if disp_dt is None:
        log(f"[클릭] 참여확인 클릭 ({path_label}, 클릭 시각 확인 불가, 처리 {exec_ms}ms)")
        return
    diff = (disp_dt - target).total_seconds()
    log(f"[클릭] 참여확인 클릭: {src} {disp_dt.strftime('%H:%M:%S.%f')[:-3]} "
        f"| 오픈 {target.strftime('%H:%M:%S')} 대비 {diff:+.3f}s | {path_label} {extra}")
    log(f"[완료] 참여확인 처리 호출 완료 (+{exec_ms}ms)")
    _LAST_TIMED_CLICK = {"lead_ms": ctx.get("lead_ms", 0.0), "after_open_s": diff, "path": path_label, "t": time.time()}


def click_confirm_modal(driver: webdriver.Chrome) -> bool:
    global _last_confirm_at, _CLICK_TIMING_CTX

    ctx = _CLICK_TIMING_CTX
    _CLICK_TIMING_CTX = None   # 1회용 — 다음 클릭에 예전 정보가 섞이지 않도록 비움

    # ── (1) 정각 대기 직후: 확인/저장/로그 없이 바로 한 번에 클릭 ───────────────────────
    #     (모달이 떠 있는지도 같은 스크립트 안에서 확인하므로 별도의 왕복이 필요 없음)
    if ctx is not None and (time.time() - _last_confirm_at) >= 0.2:
        t_click0 = time.time()
        if ctx.get("inpage"):
            # 브라우저 안에 예약해 둔 클릭의 결과를 받아옴 (안 눌렸으면 파이썬이 대신 누름)
            status, t_ms, how = _collect_inpage_result(driver)
            exec_ms = int((time.time() - t_click0) * 1000)
            ai = ctx["inpage"]
            disp_dt = (datetime(1970, 1, 1) + timedelta(milliseconds=(float(t_ms) - ai["offset_ms"]))
                       if t_ms is not None else None)
            path_label = "브라우저 내부 예약" if how == "inpage" else "예약 실패 → 파이썬 보조 클릭"
            extra = f"(브라우저 시계 오차 ±{ai['rtt_ms'] / 2:.1f}ms)"
        else:
            na = _precise_now()
            try:
                res = driver.execute_script(_CONFIRM_CLICK_JS)
            except Exception:
                res = None
            nb = _precise_now()
            exec_ms = int((time.time() - t_click0) * 1000)
            status = res[0] if res else "error"
            # 명령이 오가는 동안의 중간 시점 ≈ 실제 클릭 시점 (고정밀 타이머 기준이라 윈도우 시계 오차가 없음)
            disp_dt = na + (nb - na) / 2
            path_label = "파이썬 직접"
            extra = f"(측정 오차 ±{(nb - na).total_seconds() * 500:.0f}ms)"

        if status == "clicked":
            _last_confirm_at = t_click0
            _log_timed_click(ctx, disp_dt, path_label, extra, exec_ms)   # 클릭이 끝난 뒤에 로그 출력
            t_hide = time.time()
            while time.time() - t_hide < 2.0:
                if not is_confirm_modal_showing(driver):
                    log(f"[완료] 참여확인창 닫힘 확인 (+{int((time.time()-t_hide)*1000)}ms)")
                    break
                time.sleep(0.03)
            if ctx.get("warmed"):
                warm = _read_warm_result(driver)
                if warm:
                    log(f"[대기] 연결 예열 결과: {warm}")
            return True
        log(f"[경고] 정각 즉시 클릭 실패(status={status}) → 일반 경로로 다시 시도")

    # ── (2) 일반 경로: 모달이 뜰 때까지 기다렸다가 클릭 (즉시참여 등 정각 대기가 없는 경우) ──
    t0 = time.time()
    while time.time() - t0 < 2.0:
        if is_confirm_modal_showing(driver):
            log(f"[완료] 참여확인창 표시 감지 (+{int((time.time()-t0)*1000)}ms)")
            save_current_page_html(driver, "confirm_modal")
            break
        time.sleep(0.02)
    else:
        return False

    now = time.time()
    if now - _last_confirm_at < 0.2:
        return True
    _last_confirm_at = now

    click_local_dt = datetime.now()
    t_click0 = time.time()
    called = driver.execute_script("""
        try {
            if (typeof confirm_func === 'function') {
                confirm_func();
                if (window.jQuery) { $('#confirm_modal').modal('hide'); }
                return true;
            }
            const btn = document.querySelector("#confirm_modal button.butn.butn-yellow");
            if (btn) { btn.click(); return true; }
            return false;
        } catch(e) { return false; }
    """)
    log(f"[클릭] 참여확인 클릭 시각: PC시계 {click_local_dt.strftime('%H:%M:%S.%f')[:-3]} (정각 대기 없이 즉시 클릭)")
    if called:
        log(f"[완료] 참여확인 처리 호출 완료 (+{int((time.time()-t_click0)*1000)}ms)")

    t_hide = time.time()
    while time.time() - t_hide < 2.0:
        if not is_confirm_modal_showing(driver):
            log(f"[완료] 참여확인창 닫힘 확인 (+{int((time.time()-t_hide)*1000)}ms)")
            return True
        time.sleep(0.03)

    return True


def detail_should_abort(driver: webdriver.Chrome, current_csq: str = "") -> bool:
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "button.butn")
        for btn in btns:
            text = (btn.text or "").strip()
            if not text:
                continue

            # 종료 케이스
            if any(k in text for k in ["종료", "마감", "참여불가", "신청불가", "모집종료"]):
                log(f"[경고] 종료 버튼 감지: '{text}' (csq={current_csq})")
                _save_abort_html(driver, current_csq, "closed")
                return True

            # 날짜 파싱 케이스: "05월 21일 12시에 참여가능"
            m = re.search(r"(\d{2})월\s*(\d{2})일", text)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                today = datetime.now()
                try:
                    btn_date = today.replace(month=month, day=day)
                except ValueError:
                    continue
                if btn_date.date() != today.date():
                    log(f"[안내] 롤오버 버튼 감지: '{text}' (csq={current_csq})")
                    _save_abort_html(driver, current_csq, "rollover")
                    return True

    except Exception as e:
        log(f"[경고] detail_should_abort 오류: {e}")
    return False


def _save_abort_html(driver: webdriver.Chrome, csq: str, reason: str):
    """롤오버/종료 감지 시 HTML을 무조건 저장 (LOG_SAVE 설정 무관)"""
    try:
        html = driver.page_source or ""
        if not html:
            return

        os.makedirs("abort_logs", exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"abort_{reason}_csq{csq}_{stamp}.html"
        path = os.path.join("abort_logs", filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"[저장] abort HTML 저장: {path}")
    except Exception as e:
        log(f"[경고] abort HTML 저장 실패: {e}")


def wait_and_apply_in_detail(driver: webdriver.Chrome, current_csq: str, failed_once: set, target_open_dt: Optional[datetime], is_target: bool = False, campaign_name: str = "") -> bool:
    t0 = time.time()
    detail_url = driver.current_url
    session = build_requests_session_from_driver(driver)
    confirm_armed = False
    prejoin_started = False
    last_prejoin_try = 0.0
    PREJOIN_RETRY_SEC = 0.50
    account_try_index = 0
    account_count_hint = 0
    congestion_retry_count = 0
    sec_to_open: Optional[float] = None  # ⭐ 오픈까지 남은 시간(적응형 폴링 간격 계산용)
    name_tag = f" [{campaign_name}]" if campaign_name else ""

    while True:
        pause_point()
        close_site_popups(driver)

        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if detail_should_abort(driver, current_csq):
                log(f"[복귀] 상세에서 종료/롤오버 감지 → 감시 복귀: {current_csq}")
                goto_list_page(driver, force_get=True)
                time.sleep(0.35)
                close_site_popups(driver, force=True)
                return False

            if target_open_dt is None:
                target_open_dt = parse_open_dt_from_text(body_text)

            if target_open_dt:
                sec_to_open = (target_open_dt - datetime.now()).total_seconds()
                # 타겟 CSQ는 오픈시간이 아무리 멀어도 감시 복귀하지 않고 계속 대기
                if not is_target and sec_to_open > (HOT_WINDOW_SEC + 30) and ("참여가능" not in body_text):
                    log(f"[복귀] 오픈시간이 멀어짐({int(sec_to_open)}s) → 감시 복귀: {current_csq}")
                    goto_list_page(driver, force_get=True)
                    time.sleep(0.35)
                    close_site_popups(driver, force=True)
                    return False
                if is_target and sec_to_open > 0:
                    log(f"[타겟 대기중] csq={current_csq}{name_tag} 오픈까지 {int(sec_to_open)}초 남음")
        except Exception:
            pass

        if is_alert_modal_showing(driver) and get_alert_msg_text(driver):
            alert_result = handle_result_alert(
                driver, current_csq, failed_once,
                current_account_index=account_try_index,
                available_account_count=account_count_hint,
                campaign_name=campaign_name
            )
            if alert_result == "retry_second":
                account_try_index = 1
                prejoin_started = False
                confirm_armed = False
                time.sleep(0.10)
                continue
            if alert_result == "retry_congestion":
                congestion_retry_count += 1
                if congestion_retry_count <= CONGESTION_RETRY_MAX:
                    log(f"[재시도] 혼잡 재시도 {congestion_retry_count}/{CONGESTION_RETRY_MAX}: {current_csq}{name_tag}")
                    time.sleep(CONGESTION_RETRY_DELAY_SEC)
                    prejoin_started = False
                    confirm_armed = False
                    continue
                else:
                    log(f"[경고] 혼잡 재시도 {CONGESTION_RETRY_MAX}회 초과 → 포기: {current_csq}{name_tag}")
                    failed_once.add(current_csq)
            driver.get(LIST_URL)
            time.sleep(0.5)
            close_site_popups(driver, force=True)
            return True

        if is_confirm_modal_showing(driver) and not confirm_armed:
            if target_open_dt is None:
                log(f"[경고] 참여확인 선대기 진입했지만 오픈시간을 알 수 없음 → 즉시 참여확인 진행: {current_csq}")
            else:
                log(f"[대기] 참여확인 선대기 진입: csq={current_csq} target={target_open_dt.strftime('%m/%d %H:%M:%S')}")
                wait_until_server_second(driver, session, detail_url, target_open_dt)
            click_confirm_modal(driver)
            confirm_armed = True
            continue

        if confirm_armed:
            time.sleep(get_adaptive_poll_sec(sec_to_open))
            continue

        if is_join_accounts_modal_showing(driver):
            handled, detected_count = handle_account_modal(driver, current_csq, account_index=account_try_index)
            account_count_hint = max(account_count_hint, detected_count)
            log(f"[완료] 계정선택창 처리결과: {handled} (csq={current_csq}, index={account_try_index}, total={account_count_hint})")
            if not handled:
                failed_once.add(current_csq)
                log(f"[경고] 계정 체크 3회 실패 → 이번 실행 스킵 후 목록 복귀: {current_csq}")
                goto_list_page(driver, force_get=True)
                time.sleep(0.35)
                close_site_popups(driver, force=True)
                return False
            continue

        now = time.time()
        if not prejoin_started or (now - last_prejoin_try >= PREJOIN_RETRY_SEC):
            last_prejoin_try = now
            opened = try_open_join_without_button_search(driver, current_csq)
            if not opened:
                opened = try_click_join_button_in_detail(driver, current_csq)

            if opened:
                prejoin_started = True
                log(f"[진행] 선대기 JOIN 실행 시도: csq={current_csq}{name_tag}")
                save_current_page_html(driver, f"detail_{current_csq}_prejoin_clicked")

                kind = wait_for_post_join_event(driver, timeout=2.0)
                log(f"[감지] 선대기 JOIN 후 이벤트 감지: {kind or 'none'} (csq={current_csq})")

                if kind == "alert" and is_alert_modal_showing(driver):
                    alert_result = handle_result_alert(
                        driver, current_csq, failed_once,
                        current_account_index=account_try_index,
                        available_account_count=account_count_hint,
                        campaign_name=campaign_name
                    )
                    if alert_result == "retry_second":
                        account_try_index = 1
                        prejoin_started = False
                        confirm_armed = False
                        time.sleep(0.10)
                        continue
                    if alert_result == "retry_congestion":
                        congestion_retry_count += 1
                        if congestion_retry_count <= CONGESTION_RETRY_MAX:
                            log(f"[재시도] 혼잡 재시도 {congestion_retry_count}/{CONGESTION_RETRY_MAX}: {current_csq}{name_tag}")
                            time.sleep(CONGESTION_RETRY_DELAY_SEC)
                            prejoin_started = False
                            confirm_armed = False
                            continue
                        else:
                            log(f"[경고] 혼잡 재시도 {CONGESTION_RETRY_MAX}회 초과 → 포기: {current_csq}{name_tag}")
                            failed_once.add(current_csq)
                    goto_list_page(driver, force_get=True)
                    time.sleep(0.35)
                    close_site_popups(driver, force=True)
                    return True

                if kind == "accounts" or is_join_accounts_modal_showing(driver):
                    handled, detected_count = handle_account_modal(driver, current_csq, account_index=account_try_index)
                    account_count_hint = max(account_count_hint, detected_count)
                    log(f"[완료] 계정선택창 처리결과: {handled} (csq={current_csq}, index={account_try_index}, total={account_count_hint})")
                    if not handled:
                        failed_once.add(current_csq)
                        log(f"[경고] 계정 체크 3회 실패 → 이번 실행 스킵 후 목록 복귀: {current_csq}")
                        driver.get(LIST_URL)
                        time.sleep(0.5)
                        close_site_popups(driver)
                        return False
                    continue

                if kind == "confirm" or is_confirm_modal_showing(driver):
                    continue

        if not is_target and now - t0 >= DETAIL_MAX_WAIT_SEC:
            log(f"[복귀] 상세 대기 타임아웃 → 감시 복귀: {current_csq}")
            driver.get(LIST_URL)
            time.sleep(0.5)
            close_site_popups(driver, force=True)
            return False

        time.sleep(get_adaptive_poll_sec(sec_to_open))

def enter_detail_and_wait(driver: webdriver.Chrome, card_el, csq: str, failed_once: set, target_open_dt: Optional[datetime], is_target: bool = False, title: str = "") -> bool:
    try:
        driver.execute_script("arguments[0].click();", card_el)
        time.sleep(0.8)
        close_site_popups(driver, force=True)
        save_current_page_html(driver, f"detail_{csq}_enter")

        # 캠페인명: 카드에서 전달된 title 우선, 없으면 상세 페이지에서 여러 방식으로 추출 시도
        campaign_name = title
        if not campaign_name:
            try:
                campaign_name = driver.execute_script(r"""
                    // 0) 이 사이트 특유의 툴팁 패턴 우선 시도
                    const tip = document.querySelector('.ctooltip-text');
                    if (tip) {
                        const t = (tip.innerText || tip.textContent || '').trim();
                        if (t) return t;
                    }
                    // 1) 자주 쓰이는 제목류 셀렉터
                    const sels = ['h1', 'h2', 'h3', '[class*="title"]', '[class*="name"]',
                                  '[class*="subject"]', '.campaign-title', '.item-title', '.goods_name'];
                    for (const sel of sels) {
                        const el = document.querySelector(sel);
                        const t = el ? (el.innerText || '').trim().split('\n')[0] : '';
                        if (t) return t;
                    }
                    // 2) og:title 메타태그
                    const og = document.querySelector('meta[property="og:title"]');
                    if (og && og.content) return og.content.trim();
                    // 3) 브라우저 탭 제목 (사이트명 접미사 제거 시도)
                    if (document.title) {
                        return document.title.split(/[|\-–]/)[0].trim();
                    }
                    return '';
                """) or ""
            except Exception:
                pass
        name_tag = f" [{campaign_name}]" if campaign_name else ""
        if not campaign_name:
            log(f"[안내] csq={csq} 상품명 추출 실패 (셀렉터 미매칭) → 관리자에게 페이지 구조 확인 요청 필요")
        log(f"[진입] 상세 선점 진입: csq={csq}{name_tag}")
        return wait_and_apply_in_detail(driver, csq, failed_once, target_open_dt, is_target=is_target, campaign_name=campaign_name)
    except StopRequested:
        # ⭐ 종료/일시정지 요청으로 인한 중단은 "실패"가 아니므로 failed_once에 추가하지 않고 그대로 전파
        raise
    except Exception as e:
        log(f"[오류] 상세 진입 실패: {e}")
        failed_once.add(csq)
        try:
            goto_list_page(driver, force_get=True)
            time.sleep(0.7)
            close_site_popups(driver, force=True)
        except Exception:
            pass
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
    log("[진행] scan 시작")

    goto_list_page(driver)
    time.sleep(0.9)
    close_site_popups(driver)
    save_current_page_html(driver, "list")

    try:
        driver.execute_script("window.scrollTo(0, 0);")
    except Exception:
        pass

    log(f"[진행] 목록 진입 완료 (+{ms_from(t_scan0)}ms)")

    picked_now = None
    picked_soon = None
    seen_csq = set()

    def type_priority(v: str) -> int:
        return 0 if v == "REAL" else 1

    def inspect_visible_cards(stage_label: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        nonlocal picked_now, picked_soon

        cards = collect_visible_cards(driver)
        log(f"[진행] 카드 검사: stage={stage_label} cards={len(cards)} (+{ms_from(t_scan0)}ms)")

        local_best_now = picked_now
        local_best_soon = picked_soon

        global TARGET_CSQ
        # 콤마(,)로 구분된 문자열을 분리하여 타겟 리스트 생성 (공백 자동 제거) — 적힌 순서 = 우선순위
        target_list = [t.strip() for t in TARGET_CSQ.split(",") if t.strip()]
        # ⭐ 이 스테이지에서 조건을 만족한 타겟들을 일단 모아두고, 루프가 끝난 뒤 우선순위로 하나만 선택
        target_open_now_matches: List[Dict] = []
        target_hot_matches: List[Dict] = []

        for item_raw in cards:
            try:
                csq = (item_raw.get("csq") or "").strip()
                if not csq or csq in seen_csq:
                    continue
                seen_csq.add(csq)

                text = item_raw.get("text") or ""
                html = item_raw.get("html") or ""
                amount_attr = item_raw.get("amount") or ""
                jointime_str = item_raw.get("jointime") or ""

                # data-jointime 속성으로 오픈 시간 판단
                jointime_dt = None
                if jointime_str:
                    try:
                        jointime_dt = datetime.strptime(jointime_str, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

                open_dt = parse_open_dt_from_text(text)
                secs_to_open = None
                if open_dt:
                    secs_to_open = (open_dt - now).total_seconds()

                # ============================================================
                # ⭐ 스나이핑 모드 (여러 타겟 CSQ 일치 시 다중 처리)
                # ============================================================
                if target_list and csq in target_list:
                    # 영구저장에 있으면 타겟이라도 완전 스킵
                    if csq in success_csq:
                        log(f"[스킵] csq={csq} rsn=영구저장 완료")
                        continue
                    # ⭐ 타겟은 이번실행 실패 목록에 있어도 완전히 포기하지 않고,
                    #    매 스캔마다 최신 오픈시간을 다시 확인해서 재도전함
                    #    (일반 후보와 달리 사용자가 직접 지정한 타겟이라 계속 챙김)
                    # jointime이 이미 지났거나 참여가능 텍스트가 있으면 즉시 참여
                    jointime_passed_target = jointime_dt is not None and jointime_dt <= now
                    text_says_open_now = "참여가능" in text and ("진행중" in text or "대기중" in text)

                    # ⭐ 카드 화면 텍스트에 더 미래의 새 시간 정보(open_dt)가 보이면 그걸 우선 신뢰.
                    #    jointime 속성값이 (사이트가 아직 못 갱신해서) 과거로 멈춰있어도,
                    #    화면에 다음 회차 시간이 표시돼 있으면 "즉시 참여 가능"으로 오판하지 않고
                    #    그 새 시간을 타겟의 새 목표시각으로 갱신함.
                    has_fresh_future_time = open_dt is not None and secs_to_open is not None and secs_to_open > 0

                    if has_fresh_future_time:
                        is_open_now = False
                    else:
                        is_open_now = jointime_passed_target or text_says_open_now

                    if is_open_now:
                        # ⭐ 즉시 리턴하지 않고 일단 후보로만 모아둠 (같은 스테이지에 동시에 여러 타겟이 열렸을 수 있음)
                        target_open_now_matches.append({
                            "csq": csq,
                            "card_index": item_raw.get("idx"),
                            "title": item_raw.get("title") or "",
                        })
                    else:
                        # ⭐ jointime이 과거로 멈춰있어도 화면에 미래 시간(open_dt)이 있으면 그걸 우선 사용
                        target_open_dt = open_dt if has_fresh_future_time else (jointime_dt or open_dt)
                        target_secs = (target_open_dt - now).total_seconds() if target_open_dt else None
                        open_time_str = target_open_dt.strftime("%m/%d %H:%M:%S") if target_open_dt else "?"

                        # ⭐ 오픈 임박 기준(HOT_WINDOW_SEC) 이내일 때만 다른 후보보다 최우선으로 즉시 상세 진입.
                        #    아직 많이 남았으면 여기서 붙잡지 않고 이번 스캔에서 다른 일반 캠페인도 계속 탐색/참여.
                        #    (target_secs를 아직 모르는 경우는 정확한 오픈시각 확인을 위해 예외적으로 즉시 진입)
                        if target_secs is None or target_secs <= HOT_WINDOW_SEC:
                            wait_msg = f"{int(target_secs)}초 남음" if target_secs and target_secs > 0 else "상세 페이지에서 오픈시간 확인 예정"
                            # ⭐ 이것도 즉시 리턴하지 않고 후보로만 모아둠
                            target_hot_matches.append({
                                "csq": csq,
                                "card_index": item_raw.get("idx"),
                                "title": item_raw.get("title") or "",
                                "open_dt": target_open_dt,
                                "secs_to_open": target_secs,
                                "wait_msg": wait_msg,
                                "open_time_str": open_time_str,
                            })
                        else:
                            title_tag = f" [{item_raw.get('title') or ''}]" if item_raw.get("title") else ""
                            log(f"[타겟] 타겟 {csq}{title_tag} 아직 여유 있음 ({int(target_secs)}초 남음, 시작={open_time_str}, 임박기준 {HOT_WINDOW_SEC}초) → 지금은 다른 캠페인 탐색/참여 계속")
                    continue
                # ============================================================

                ctype = detect_type(text, html)
                deposit_mode = detect_deposit_mode(text, html)
                price = resolve_card_price(amount_attr, text)
                shop = detect_shop(text, html)

                eligible_base = True
                skip_reason = ""

                if csq in success_csq:
                    eligible_base = False
                    skip_reason = "saved"
                elif csq in account_fail_csq:
                    eligible_base = False
                    skip_reason = "acct_fail"
                elif csq in failed_once:
                    eligible_base = False
                    skip_reason = "fail1"
                else:
                    if ctype == "ETC" and price < MIN_PRICE_ETC:
                        eligible_base = False
                        skip_reason = "low_etc"
                    elif ctype == "REAL" and price < MIN_PRICE_REAL:
                        eligible_base = False
                        skip_reason = "low_real"
                    elif ctype == "UNKNOWN":
                        eligible_base = False
                        skip_reason = "unk_type"

                    if eligible_base and not is_allowed_deposit_mode(ACTIVE_SITE_KEY, ctype, deposit_mode):
                        eligible_base = False
                        skip_reason = f"deposit={deposit_mode}"

                item = {
                    "card_index": item_raw.get("idx"),
                    "csq": csq,
                    "title": item_raw.get("title") or "",
                    "text": text,
                    "type": ctype,
                    "shop": shop,
                    "price": price,
                    "amount_attr": amount_attr,
                    "open_dt": open_dt,
                    "secs_to_open": secs_to_open,
                }

                def item_sort_key(i: dict) -> tuple:
                    # 1순위: 배송타입 (REAL=0, ETC=1)
                    # 2순위: 쇼핑몰 (NAVER=0, OTHER=1)
                    # 3순위: 가격 내림차순
                    return (type_priority(i["type"]), 0 if i["shop"] == "NAVER" else 1, -i["price"])

                # jointime 기반: 오픈 시간이 현재보다 과거면 즉시 참여 가능
                jointime_passed = jointime_dt is not None and jointime_dt <= now
                eligible_now = eligible_base and (
                    jointime_passed
                    or ("참여가능" in text and ("진행중" in text or "대기중" in text))
                )
                eligible_soon = eligible_base and open_dt and secs_to_open is not None and (0 < secs_to_open <= HOT_WINDOW_SEC)

                if eligible_now:
                    log(f"[타겟] 즉시 후보 최초감지: {short_card_desc(csq, ctype, price, secs_to_open)} shop={shop} stage={stage_label} (+{ms_from(t_scan0)}ms)")
                    if (
                        local_best_now is None
                        or item_sort_key(item) < item_sort_key(local_best_now)
                    ):
                        local_best_now = item

                elif eligible_soon:
                    log(f"[대기] 선대기 후보 감지: {short_card_desc(csq, ctype, price, secs_to_open)} shop={shop} stage={stage_label} (+{ms_from(t_scan0)}ms)")
                    if local_best_soon is None:
                        local_best_soon = item
                    else:
                        cur_key = (item["open_dt"], type_priority(item["type"]), 0 if item["shop"] == "NAVER" else 1, -item["price"])
                        prev_key = (local_best_soon["open_dt"], type_priority(local_best_soon["type"]), 0 if local_best_soon["shop"] == "NAVER" else 1, -local_best_soon["price"])
                        if cur_key < prev_key:
                            local_best_soon = item
                else:
                    if eligible_base:
                        if secs_to_open is not None and secs_to_open > 0:
                            hold_rsn = f"open_in={int(secs_to_open)}s"
                        else:
                            hold_rsn = "not_open"
                        log(f"[진행] 후보 보류: csq={csq} type={ctype} pr={price:,} rsn={hold_rsn} stg={stage_label}")
                    else:
                        log(f"[스킵] csq={csq} rsn={skip_reason} pr={price:,} stg={stage_label}")

            except Exception as e:
                log(f"[경고] 카드 검사 오류 stage={stage_label}: {e}")

        # ⭐ 이 스테이지에서 동시에 조건을 만족한 타겟이 여러 개면, 타겟 CSQ 칸에 적힌 순서(우선순위)로 하나만 선택.
        #    타겟은 항상 일반 후보(local_best_now/soon)보다 우선하므로 여기서 결정되면 그대로 반환.
        if target_open_now_matches:
            if len(target_open_now_matches) > 1:
                order_str = ", ".join(m["csq"] for m in sorted(target_open_now_matches, key=lambda m: target_list.index(m["csq"])))
                log(f"[타겟] 동시에 {len(target_open_now_matches)}개 타겟 즉시참여 가능 감지 ({order_str}) → 적힌 순서상 우선순위 1순위 선택")
            best = min(target_open_now_matches, key=lambda m: target_list.index(m["csq"]))
            log(f"[타겟] [타겟 발견] {best['csq']} 즉시 참여 가능 → 즉시 참여 시도!")
            picked_now = {"csq": best["csq"], "card_index": best["card_index"], "price": 0, "type": "TARGET", "title": best["title"]}
            picked_soon = local_best_soon
            return picked_now, picked_soon

        if target_hot_matches:
            if len(target_hot_matches) > 1:
                order_str = ", ".join(m["csq"] for m in sorted(target_hot_matches, key=lambda m: target_list.index(m["csq"])))
                log(f"[타겟] 동시에 {len(target_hot_matches)}개 타겟 오픈 임박 감지 ({order_str}) → 적힌 순서상 우선순위 1순위 선택")
            best = min(target_hot_matches, key=lambda m: target_list.index(m["csq"]))
            log(f"[타겟] [타겟 발견] {best['csq']} ({best['wait_msg']}, 시작={best['open_time_str']}) → 오픈 임박, 다른 후보보다 최우선으로 즉시 상세 진입 후 대기!")
            picked_now = local_best_now
            picked_soon = {
                "csq": best["csq"],
                "card_index": best["card_index"],
                "price": 0,
                "type": "TARGET",
                "title": best["title"],
                "open_dt": best["open_dt"],
                "secs_to_open": best["secs_to_open"],
            }
            return picked_now, picked_soon

        picked_now = local_best_now
        picked_soon = local_best_soon
        return picked_now, picked_soon

    now_item, soon_item = inspect_visible_cards("top")
    if now_item is not None:
        log(f"[완료] 즉시 후보 확정(top) (+{ms_from(t_scan0)}ms)")
        return now_item, soon_item
    if soon_item is not None and soon_item.get("type") == "TARGET":
        log(f"[타겟] 타겟 CSQ 확정(top) → 즉시 상세 진입 (+{ms_from(t_scan0)}ms)")
        return now_item, soon_item

    for i in range(MAX_SCROLL):
        t_scroll = time.time()
        pause_point()
        driver.execute_script("window.scrollBy(0, 1500);")
        time.sleep(0.30)

        if i % 3 == 2:
            close_site_popups(driver)

        log(f"[진행] 스크롤 완료 #{i+1}/{MAX_SCROLL} (+{int((time.time()-t_scroll)*1000)}ms / total={ms_from(t_scan0)}ms)")

        now_item, soon_item = inspect_visible_cards(f"scroll_{i+1}")
        if now_item is not None:
            log(f"[완료] 즉시 후보 확정(scroll_{i+1}) (+{ms_from(t_scan0)}ms)")
            return now_item, soon_item
        if soon_item is not None and soon_item.get("type") == "TARGET":
            log(f"[타겟] 타겟 CSQ 확정(scroll_{i+1}) → 즉시 상세 진입 (+{ms_from(t_scan0)}ms)")
            return now_item, soon_item

    log(f"[완료] scan 종료: now={'Y' if picked_now else 'N'} soon={'Y' if picked_soon else 'N'} total={ms_from(t_scan0)}ms")
    return picked_now, picked_soon


# =========================
# 메인
# =========================
def main():
    apply_site_config(choose_site())

    if not os.path.exists(SUCCESS_CSQ_FILE):
        open(SUCCESS_CSQ_FILE, "w", encoding="utf-8").close()
    if PERSIST_ACCOUNT_FAIL and ACCOUNT_FAIL_CSQ_FILE and not os.path.exists(ACCOUNT_FAIL_CSQ_FILE):
        open(ACCOUNT_FAIL_CSQ_FILE, "w", encoding="utf-8").close()

    email = os.environ.get("DBG_EMAIL") or input(f"[{SITE_NAME}] 이메일 입력: ").strip()
    password = os.environ.get("DBG_PASS") or os.environ.get("DBG_PASSWORD")
    if not password:
        password = getpass(f"[{SITE_NAME}] 비밀번호 입력(화면에 안 보임): ").strip()

    log(
        f"[설정] 사이트 설정 적용: site={SITE_NAME}, allowed_deposit_modes={ALLOWED_DEPOSIT_MODES}, "
        f"persist_account_fail={PERSIST_ACCOUNT_FAIL}, retry_second={RETRY_SECOND_ACCOUNT}"
    )

    driver = build_driver(headless=HEADLESS)
    failed_once = set()

    try:
        login_dbg(driver, email, password)
        setup_pause_handlers()
        log(f"[진행] 자동 감시 시작 [{SITE_NAME}] (Ctrl+C 종료 / Ctrl+Z 일시정지·재개)")

        loop_no = 0
        while True:
            loop_no += 1
            begin_loop_log(loop_no)
            if LOG_SAVE:
                log(f"[진행] 루프 로그 파일 시작: {CURRENT_LOOP_LOG_FILE}")
            else:
                log("[진행] 디스크 로그 저장 OFF 상태로 루프 시작")

            loop_status = "done"
            try:
                pause_point()
                t_loop0 = time.time()
                picked_now, picked_soon = scan_and_pick(driver, failed_once)
                log(f"[진행] scan_and_pick 반환 완료 (+{int((time.time()-t_loop0)*1000)}ms)")

                # 1) 오픈 임박 → 상세 선점
                if picked_soon is not None:
                    csq = picked_soon["csq"]
                    price = picked_soon["price"]
                    target_open_dt = picked_soon.get("open_dt")
                    open_text = target_open_dt.strftime("%H:%M:%S") if target_open_dt else "?"
                    picked_title = picked_soon.get("title") or ""
                    title_tag = f" [{picked_title}]" if picked_title else ""
                    log(f"[대기] 오픈 임박 후보 발견(<= {HOT_WINDOW_SEC}s) → 상세 선점: csq={csq}{title_tag} price={price:,} open={open_text}")

                    card_el = get_card_element_by_index(driver, picked_soon["card_index"])
                    if card_el is None:
                        log(f"[경고] 카드 재조회 실패(soon): {csq}")
                        goto_list_page(driver, force_get=True)
                        time.sleep(0.25)
                        loop_status = "soon_card_missing"
                        continue

                    enter_detail_and_wait(driver, card_el, csq, failed_once, target_open_dt, title=picked_title)
                    time.sleep(0.25)
                    loop_status = f"soon:{csq}"
                    continue

                # 2) 즉시 참여가능 → 즉시 참여
                if picked_now is not None:
                    csq = picked_now["csq"]
                    picked_title_now = picked_now.get("title") or ""
                    title_tag_now = f" [{picked_title_now}]" if picked_title_now else ""
                    log(f"[타겟] 즉시 참여 후보 최종선정: csq={csq}{title_tag_now} price={picked_now['price']:,} → 참여 시도")

                    try:
                        card_el = get_card_element_by_index(driver, picked_now["card_index"])
                        if card_el is None:
                            log(f"[경고] 카드 재조회 실패(now): {csq}")
                            goto_list_page(driver, force_get=True)
                            time.sleep(0.25)
                            loop_status = "now_card_missing"
                            continue

                        t_join0 = time.time()

                        opened = fast_enter_detail_and_click_join(
                            driver,
                            card_el,
                            csq,
                            timeout=0.6,
                            popup_retry_after=0.12,
                        )

                        if opened == "CLICKED":
                            log(f"[진행] 즉시참여 상세진입→JOIN 소요: {int((time.time()-t_join0)*1000)}ms (csq={csq})")
                            process_immediate_join_flow(driver, csq, failed_once, campaign_name=picked_title_now)
                            loop_status = f"now:{csq}"
                        elif opened.startswith("CLOSED"):
                            btn_txt = opened.split(":", 1)[-1] if ":" in opened else opened
                            log(f"[경고] 버튼 텍스트 마감/종료 감지 → 이번 실행 스킵: {csq} (텍스트: {btn_txt})")
                            failed_once.add(csq)
                            goto_list_page(driver, force_get=True)
                            time.sleep(0.35)
                            close_site_popups(driver, force=True)
                            loop_status = f"now_closed:{csq}"
                        elif opened.startswith("WAIT"):
                            btn_txt = opened.split(":", 1)[-1] if ":" in opened else opened
                            log(f"[대기] 버튼 텍스트 오픈 전(시간 표기) 감지 → 선점 대기 모드 전환: {csq} (텍스트: {btn_txt})")
                            open_dt = parse_open_dt_from_text(btn_txt)
                            wait_and_apply_in_detail(driver, csq, failed_once, open_dt, campaign_name=picked_title_now)
                            time.sleep(0.25)
                            loop_status = f"soon:{csq}"
                        else:
                            # ⭐ 새롭게 방어되는 구간! (버튼이 아예 안 보일 때)
                            log(f"[경고] 참여 버튼 없음/지연 → 이번 실행 스킵: {csq}")
                            failed_once.add(csq)
                            goto_list_page(driver, force_get=True)
                            time.sleep(0.35)
                            close_site_popups(driver, force=True)
                            loop_status = f"now_open_failed:{csq}"

                    except StopRequested:
                        raise
                    except Exception as e:
                        log(f"[오류] 즉시 참여 처리 오류: {e}")
                        failed_once.add(csq)
                        goto_list_page(driver, force_get=True)
                        time.sleep(0.35)
                        close_site_popups(driver, force=True)
                        loop_status = f"now_error:{csq}"

                    continue

                # 3) 후보 없음 → 로그만
                log(f"[{SITE_NAME}_NONE] 후보 없음")
                loop_status = "none"
                time.sleep(BASE_REFRESH_INTERVAL)

            finally:
                end_loop_log(loop_status)

    except KeyboardInterrupt:
        log("[일시정지] Ctrl+C 종료")
    finally:
        log("[진행] 크롬 종료 중...")
        quit_ok = True
        try:
            driver.quit()
        except Exception as e:
            quit_ok = False
            log(f"[경고] driver.quit() 실패, 강제 정리 시도: {e}")
        if not quit_ok:
            _force_kill_driver_processes(driver)
        log("[완료] 종료 완료")


def _force_kill_driver_processes(driver) -> None:
    """driver.quit()이 뭔가 남겼을 경우를 대비해 남은 chromedriver/chrome 프로세스를 강제 정리"""
    pid = None
    try:
        pid = driver.service.process.pid
    except Exception:
        pid = None
    if not pid:
        return
    try:
        import psutil
        try:
            parent = psutil.Process(pid)
            for p in parent.children(recursive=True):
                try:
                    p.kill()
                except Exception:
                    pass
            parent.kill()
            log(f"[정리] 남은 드라이버 프로세스 강제 종료 완료 (pid={pid})")
        except psutil.NoSuchProcess:
            pass
    except ImportError:
        try:
            import subprocess, platform
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                capture_output=True, timeout=5)
            else:
                os.kill(pid, 9)
            log(f"[정리] 남은 드라이버 프로세스 강제 종료 시도 (pid={pid})")
        except Exception as e:
            log(f"[경고] 드라이버 프로세스 강제 종료 실패: {e}")


if __name__ == "__main__":
    main()
