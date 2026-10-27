# -*- coding: utf-8 -*-
"""
GT 자동 감시 GUI
- gui.py  : tkinter 진입점 (이 파일)
- sum_260609.py : 실제 감시 로직 (같은 폴더에 위치)

실행 방법:
    python gui.py
"""

GUI_LAST_MODIFIED = "2026-10-27 13:45"  # ⭐ 실시간 핑 표시 기능 추가

import os
import sys
import re
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox
import importlib
import importlib.util
import types
from datetime import datetime, timedelta

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

_LOGIC_FILE = resource_path("sum_260609.py")


# ── GitHub 자동 업데이트 ─────────────────────────────────────────────────
GITHUB_OWNER  = "jisoocori"
GITHUB_REPO   = "gt-bot"
GITHUB_BRANCH = "main"
AUTO_UPDATE_ENABLED = False   # 덮어쓰기 방지를 위해 꺼둠

def _check_updates_in_background(on_result=None):
    if not AUTO_UPDATE_ENABLED:
        return

    def _worker():
        import urllib.request

        def _norm(b: bytes) -> bytes:
            return b.replace(b"\r\n", b"\n")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        updated_files = []

        for fname in ("gui.py", "sum_260609.py"):
            local_path = os.path.join(base_dir, fname)
            url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{fname}"
            try:
                with urllib.request.urlopen(url, timeout=4) as resp:
                    remote_bytes = resp.read()
            except Exception as e:
                print(f"[업데이트 확인 실패] {fname}: {e}")
                continue
            if not remote_bytes:
                continue
            try:
                with open(local_path, "rb") as f:
                    local_bytes = f.read()
            except FileNotFoundError:
                local_bytes = b""
            if _norm(remote_bytes) != _norm(local_bytes):
                try:
                    with open(local_path, "wb") as f:
                        f.write(remote_bytes)
                    print(f"[업데이트 적용됨] {fname} (다음 실행부터 반영됩니다)")
                    updated_files.append(fname)
                except Exception as e:
                    print(f"[업데이트 저장 실패] {fname}: {e}")

        if on_result:
            on_result(updated_files)

    th = threading.Thread(target=_worker, daemon=True)
    th.start()


# ── 로직 모듈 동적 import (같은 폴더 기준) ──────────────────────────────────
_LOGIC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sum_260609.py")

def _load_logic():
    spec = importlib.util.spec_from_file_location("gt_logic", _LOGIC_FILE)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules["gt_logic"] = mod
    spec.loader.exec_module(mod)
    return mod

try:
    logic = _load_logic()
except FileNotFoundError:
    logic = None   # 로직 파일 없으면 UI만 띄움 (개발 테스트용)

# ── 설정 저장 파일 ────────────────────────────────────────────────────────────
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_settings.txt")

def _save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            for k, v in data.items():
                f.write(f"{k}={v}\n")
    except Exception:
        pass

def _load_settings() -> dict:
    result = {}
    if not os.path.exists(SETTINGS_FILE):
        return result
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


# ── csq 파일 읽기/쓰기 (쉼표 구분 ↔ 줄바꿈 변환) ───────────────────────────

def _csq_file_to_text(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        raw = open(path, "r", encoding="utf-8").read().strip()
        if not raw:
            return ""
        items = [x.strip() for x in raw.split(",") if x.strip()]
        return "\n".join(items)
    except Exception:
        return ""


def _text_to_csq_set(text: str) -> set:
    return set(x.strip() for x in text.splitlines() if x.strip())


def _text_to_csq_list_ordered(text: str) -> list:
    seen = set()
    out = []
    for line in text.splitlines():
        csq = line.strip()
        if csq and csq not in seen:
            seen.add(csq)
            out.append(csq)
    return out


def _write_csq_file(path: str, text: str):
    if not path:
        return
    items = [x.strip() for x in text.splitlines() if x.strip()]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(",".join(items))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  메인 GUI 클래스
# ══════════════════════════════════════════════════════════════════════════════

class GTApp(tk.Tk):
    def __init__(self):
        super().__init__()
        folder_name = os.path.basename(os.getcwd())
        logic_build = getattr(logic, "LOGIC_LAST_MODIFIED", "?") if logic else "?"
        self.title(f"GT 자동 감시 GUI - [{folder_name}]  (GUI {GUI_LAST_MODIFIED} / 로직 {logic_build})")
        self.resizable(True, True)
        self.minsize(900, 640)

        # 상태
        self._running   = False
        self._paused    = False
        self._thread    = None
        self._failed_once: set = set()   # 이번실행 csq (로직과 공유)
        self._stop_event = threading.Event()
        self._prev_site     = "gt"   
        self._initializing  = True   
        self._acct_list     = []     
        self._results_win_txt = None  

        # 설정 로드
        self._settings = _load_settings()

        self._build_ui()
        self._restore_settings()

        # 로직 모듈의 log() 를 GUI 출력으로 교체
        if logic:
            logic.log = self._gui_log

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(300, lambda: _check_updates_in_background(self._on_update_check_done))

    def _on_update_check_done(self, updated_files):
        if not updated_files:
            return
        names = ", ".join(updated_files)
        self.after(0, self._append_log,
                    f"[안내] 새 버전 받음: {names} — 다음 실행부터 반영됩니다 (지금 재시작해도 됨)\n")

    # ── UI 구성 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = dict(padx=6, pady=3)

        # ── 1행: 로그인/기본 ─────────────────────────────────────────────────
        fr_login = tk.LabelFrame(self, text="로그인 / 기본", padx=6, pady=4)
        fr_login.pack(fill=tk.X, **PAD)

        # 사이트 선택
        tk.Label(fr_login, text="사이트").grid(row=0, column=0, sticky="w")
        self.var_site = tk.StringVar(value="gt")
        self.var_site.trace_add("write", self._on_site_changed)
        site_keys = list(logic.SITE_CONFIGS.keys()) if logic else ["gt", "dbd"]
        site_menu = tk.OptionMenu(fr_login, self.var_site, *site_keys)
        site_menu.config(width=6)
        site_menu.grid(row=0, column=1, sticky="w", padx=(4, 20))

        # 이메일
        tk.Label(fr_login, text="이메일").grid(row=0, column=2, sticky="w")
        self.var_email = tk.StringVar()
        self.ent_email = tk.Entry(fr_login, textvariable=self.var_email, width=32)
        self.ent_email.grid(row=0, column=3, sticky="ew", padx=(4, 20))

        # 비밀번호
        tk.Label(fr_login, text="비밀번호").grid(row=0, column=4, sticky="w")
        self.var_pw = tk.StringVar()
        self.ent_pw = tk.Entry(fr_login, textvariable=self.var_pw, show="*", width=32)
        self.ent_pw.grid(row=0, column=5, sticky="ew", padx=4)
        fr_login.columnconfigure(3, weight=1)
        fr_login.columnconfigure(5, weight=1)

        # 예약 시작
        tk.Label(fr_login, text="예약시작(MM/DD HH:MM:SS)").grid(row=0, column=6, sticky="w", padx=(20, 2))
        self.var_sched_time = tk.StringVar(value=self._settings.get("sched_time", ""))
        self.ent_sched = tk.Entry(fr_login, textvariable=self.var_sched_time, width=16)
        self.ent_sched.grid(row=0, column=7, sticky="w", padx=(0, 3))
        self.btn_sched = tk.Button(fr_login, text="예약", width=6, command=self._toggle_schedule)
        self.btn_sched.grid(row=0, column=8, sticky="w", padx=3)

        # 예약 종료
        tk.Label(fr_login, text="예약종료(MM/DD HH:MM:SS)").grid(row=1, column=6, sticky="w", padx=(20, 2), pady=(4,0))
        self.var_stop_sched_time = tk.StringVar(value=self._settings.get("stop_sched_time", ""))
        self.ent_stop_sched = tk.Entry(fr_login, textvariable=self.var_stop_sched_time, width=16)
        self.ent_stop_sched.grid(row=1, column=7, sticky="w", padx=(0, 3), pady=(4,0))
        self.btn_stop_sched = tk.Button(fr_login, text="예약", width=6, command=self._toggle_stop_schedule)
        self.btn_stop_sched.grid(row=1, column=8, sticky="w", padx=3, pady=(4,0))

        # ── row=1: 계정 드롭다운 ──────────────────────────────────────────────
        tk.Label(fr_login, text="계정선택").grid(row=1, column=0, sticky="w", pady=(4,0))
        self.var_acct = tk.StringVar(value="(저장된 계정 없음)")
        self._acct_menu = tk.OptionMenu(fr_login, self.var_acct, "(저장된 계정 없음)")
        self._acct_menu.config(width=32, anchor="w")
        self._acct_menu.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(4,4), pady=(4,0))
        tk.Button(fr_login, text="➕ 추가", width=7,
                  command=self._add_account).grid(row=1, column=4, padx=(4,2), pady=(4,0), sticky="w")
        tk.Button(fr_login, text="🗑 삭제", width=7,
                  command=self._del_account).grid(row=1, column=5, padx=(2,4), pady=(4,0), sticky="w")

        # ── row=2: 체크박스 ───────────────────────────────────────────────────
        self.var_remember_email = tk.BooleanVar()
        self.var_headless       = tk.BooleanVar()
        self.var_remember_pw    = tk.BooleanVar()
        self.var_biz_only       = tk.BooleanVar()

        tk.Checkbutton(fr_login, text="이메일 기억", variable=self.var_remember_email).grid(
            row=2, column=1, sticky="w")
        tk.Checkbutton(fr_login, text="헤드리스",   variable=self.var_headless).grid(
            row=2, column=2, sticky="w")
        tk.Checkbutton(fr_login, text="비밀번호 기억", variable=self.var_remember_pw).grid(
            row=2, column=3, sticky="w")

        # ── 2행: 실시간 설정 ─────────────────────────────────────────────────
        fr_set = tk.LabelFrame(self, text="실시간 설정", padx=6, pady=4)
        fr_set.pack(fill=tk.X, **PAD)

        labels_vars = [
            ("기타배송 최소금액", "min_etc"),
            ("실배송 최소금액",   "min_real"),
            ("기본 스캔 주기(초)", "scan_interval"),
            ("오픈 임박 기준(초)", "hot_window"),
            ("스크롤 횟수",       "max_scroll"),
            ("JOIN 타임아웃(초)", "join_timeout"),
        ]
        self._setting_vars = {}
        for col, (lbl, key) in enumerate(labels_vars):
            tk.Label(fr_set, text=lbl).grid(row=0, column=col*2, sticky="w", padx=(8,2))
            var = tk.StringVar()
            tk.Entry(fr_set, textvariable=var, width=8).grid(row=0, column=col*2+1, sticky="ew", padx=(0,8))
            self._setting_vars[key] = var

        fr_checks = tk.Frame(fr_set)
        fr_checks.grid(row=1, column=0, columnspan=12, sticky="w")
        tk.Checkbutton(fr_checks, text="업체입금만 신청", variable=self.var_biz_only).pack(
            side=tk.LEFT, padx=(8, 0))
        self.var_log_save = tk.BooleanVar(value=False)
        self.var_show_skip = tk.BooleanVar(value=False)
        self.var_brief = tk.BooleanVar(value=False)
        self.var_only_done = tk.BooleanVar(value=False)
        tk.Checkbutton(fr_checks, text="스킵 로그 표시", variable=self.var_show_skip).pack(
            side=tk.LEFT, padx=(8, 0))
        tk.Checkbutton(fr_checks, text="텍스트/HTML 로그 파일 저장", variable=self.var_log_save,
                       command=self._on_log_save_changed).pack(side=tk.LEFT, padx=(8, 0))
        tk.Checkbutton(fr_checks, text="간략히 보기 ([진행] 숨김)", variable=self.var_brief).pack(
            side=tk.LEFT, padx=(8, 0))
        tk.Checkbutton(fr_checks, text="[완료]만 보기", variable=self.var_only_done, fg="darkgreen").pack(
            side=tk.LEFT, padx=(8, 0))


        # ── 3행: 버튼 + 상태 ─────────────────────────────────────────────────
        fr_btn = tk.Frame(self)
        fr_btn.pack(fill=tk.X, **PAD)

        btn_cfg = dict(width=8)
        self.btn_start  = tk.Button(fr_btn, text="시작",        command=self._start,  **btn_cfg)
        self.btn_pause  = tk.Button(fr_btn, text="일시정지",    command=self._pause,  **btn_cfg, state=tk.DISABLED)
        self.btn_resume = tk.Button(fr_btn, text="재개",        command=self._resume, **btn_cfg, state=tk.DISABLED)
        self.btn_stop   = tk.Button(fr_btn, text="종료",        command=self._stop,   **btn_cfg, state=tk.DISABLED)
        self.btn_clrlog = tk.Button(fr_btn, text="로그 지우기", command=self._clear_log, **btn_cfg)
        self.btn_reset  = tk.Button(fr_btn, text="저장값 초기화", command=self._reset_storage, width=10)
        self.btn_export = tk.Button(fr_btn, text="로그 저장", command=self._export_log, width=8)
        self.btn_results = tk.Button(fr_btn, text="결과 모아보기", command=self._show_results_only, width=10, fg="darkgreen")

        for b in (self.btn_start, self.btn_pause, self.btn_resume,
                  self.btn_stop, self.btn_clrlog, self.btn_reset, self.btn_export, self.btn_results):
            b.pack(side=tk.LEFT, padx=3)

        self.lbl_status = tk.Label(fr_btn, text="상태: 대기중", fg="gray")
        self.lbl_status.pack(side=tk.LEFT, padx=12)

        # ⭐ 핑(Ping) 표시 라벨 (우측에 배치)
        self.lbl_ping = tk.Label(fr_btn, text="서버 핑: 측정 대기", fg="#d32f2f", font=("Consolas", 10, "bold"))
        self.lbl_ping.pack(side=tk.RIGHT, padx=12)

        self._sched_armed  = False
        self._sched_target = None  
        self._stop_sched_armed  = False
        self._stop_sched_target = None  

        # ── 4행: 로그 + csq 패널 ─────────────────────────────────────────────
        fr_panels = tk.Frame(self)
        fr_panels.pack(fill=tk.BOTH, expand=True, **PAD)

        # 실시간 로그
        fr_log = tk.LabelFrame(fr_panels, text="실시간 로그")
        fr_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0,4))
        self.txt_log = scrolledtext.ScrolledText(fr_log, state=tk.DISABLED,
                                                  font=("Consolas", 9), wrap=tk.WORD)
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        self._log_auto_scroll = True  
        self.txt_log.bind("<MouseWheel>", self._on_log_scroll)
        self.txt_log.bind("<Button-4>",   self._on_log_scroll)  
        self.txt_log.bind("<Button-5>",   self._on_log_scroll)  
        self.txt_log.vbar.bind("<ButtonRelease-1>", self._on_log_scrollbar_release)

        # 영구저장 csq
        fr_perm = tk.LabelFrame(fr_panels, text="영구저장 csq")
        fr_perm.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=4, ipadx=4)
        fr_perm.configure(width=160)
        self.txt_perm = scrolledtext.ScrolledText(fr_perm, width=18,
                                                   font=("Consolas", 9), wrap=tk.NONE,
                                                   undo=True, autoseparators=True, maxundo=-1)
        self.txt_perm.pack(fill=tk.BOTH, expand=True)
        self.txt_perm.bind("<FocusOut>", self._on_perm_focusout)
        tk.Button(fr_perm, text="초기화", fg="darkred",
                  command=self._clear_perm).pack(fill=tk.X, padx=2, pady=2)

        # 🎯 타겟 CSQ
        fr_target = tk.LabelFrame(fr_panels, text="🎯 타겟 CSQ", fg="blue")
        fr_target.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(4,0), ipadx=4)
        fr_target.configure(width=160)
        tk.Label(fr_target, text="한 줄에 하나씩\n(조건 무시·최우선 신청)",
                 fg="gray", font=("Consolas", 8), justify="left").pack(anchor="w", padx=2)
        self.txt_target = scrolledtext.ScrolledText(fr_target, width=18,
                                                     font=("Consolas", 9), wrap=tk.NONE,
                                                     bg="#e8f0ff",
                                                     undo=True, autoseparators=True, maxundo=-1)
        self.txt_target.pack(fill=tk.BOTH, expand=True)
        self.txt_target.bind("<KeyRelease>", lambda e: self._refresh_target_info_panel())
        tk.Button(fr_target, text="초기화", fg="blue",
                  command=lambda: (self.txt_target.delete("1.0", tk.END), self._refresh_target_info_panel())
                  ).pack(fill=tk.X, padx=2, pady=2)

        # 🎯 타겟 정보
        fr_target_info = tk.LabelFrame(fr_panels, text="타겟 정보(오픈시간·상품명)", fg="blue", width=300)
        fr_target_info.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(4,0), ipadx=4)
        fr_target_info.pack_propagate(False)  
        self.txt_target_info = scrolledtext.ScrolledText(fr_target_info, width=44,
                                                           font=("맑은 고딕", 10), wrap=tk.WORD,
                                                           bg="#fafafa", state=tk.DISABLED,
                                                           borderwidth=0, padx=6, pady=4)
        self.txt_target_info.pack(fill=tk.BOTH, expand=True)
        self.txt_target_info.tag_configure("csq", font=("Consolas", 11, "bold"), foreground="#1a4fa0",
                                            spacing1=4, spacing3=2)
        self.txt_target_info.tag_configure("open", font=("맑은 고딕", 10, "bold"), foreground="#0a7a2f")
        self.txt_target_info.tag_configure("title", font=("맑은 고딕", 10), foreground="#222222",
                                            spacing3=2)
        self.txt_target_info.tag_configure("pending", font=("맑은 고딕", 9), foreground="#999999",
                                            spacing3=2)
        self.txt_target_info.tag_configure("sep", foreground="#dddddd")
        self._target_info = {}  

    # ── 타겟 정보 패널 ────────────────────────────────────────────────────────

    _TARGET_INFO_PATTERNS = [
        re.compile(r"\[타겟\] \[타겟 발견\] (\d+)(?: \[(.*?)\])? \(.*?시작=([0-9/: ]+)\)"),
        re.compile(r"\[타겟\] 타겟 (\d+)(?: \[(.*?)\])? 아직 여유 있음 \(.*?시작=([0-9/: ]+),"),
    ]
    _TARGET_INFO_OPEN_NOW_PATTERN = re.compile(r"\[타겟\] \[타겟 발견\] (\d+) 즉시 참여 가능")

    def _parse_target_info(self, msg: str):
        for pat in self._TARGET_INFO_PATTERNS:
            m = pat.search(msg)
            if m:
                csq, title, open_str = m.group(1), m.group(2) or "", m.group(3).strip()
                prev = self._target_info.get(csq)
                if not title and prev and prev.get("title"):
                    title = prev["title"]
                new_info = {"open": open_str, "title": title}
                if prev != new_info:
                    self._target_info[csq] = new_info
                    self.after(0, self._refresh_target_info_panel)
                return
        m2 = self._TARGET_INFO_OPEN_NOW_PATTERN.search(msg)
        if m2:
            csq = m2.group(1)
            prev = self._target_info.get(csq)
            title = (prev or {}).get("title", "")
            marker = "지금 즉시참여 시도 중 (다음 회차 시간 미확인)"
            new_info = {"open": marker, "title": title}
            if prev != new_info:
                self._target_info[csq] = new_info
                self.after(0, self._refresh_target_info_panel)

    def _refresh_target_info_panel(self):
        try:
            target_csqs = _text_to_csq_list_ordered(self.txt_target.get("1.0", tk.END))
        except Exception:
            target_csqs = list(self._target_info.keys())

        self.txt_target_info.config(state=tk.NORMAL)
        self.txt_target_info.delete("1.0", tk.END)
        for i, csq in enumerate(target_csqs):
            info = self._target_info.get(csq)
            self.txt_target_info.insert(tk.END, f"csq {csq}\n", "csq")
            if info:
                self.txt_target_info.insert(tk.END, f"  {info['open']}\n", "open")
                title = info["title"] or "(상품명 미확인)"
                self.txt_target_info.insert(tk.END, f"  {title}\n", "title")
            else:
                self.txt_target_info.insert(tk.END, "  아직 스캔에서 발견 안 됨\n", "pending")
            if i < len(target_csqs) - 1:
                self.txt_target_info.insert(tk.END, "─" * 16 + "\n", "sep")
        self.txt_target_info.config(state=tk.DISABLED)

    def _sort_target_by_time(self):
        try:
            target_csqs = _text_to_csq_list_ordered(self.txt_target.get("1.0", tk.END))
        except Exception:
            return
        if len(target_csqs) < 2:
            return

        def _parse_open_dt(open_str: str):
            try:
                dt = datetime.strptime(open_str, "%m/%d %H:%M:%S")
                return dt.replace(year=datetime.now().year)
            except Exception:
                return None

        now = datetime.now()

        def sort_key(idx_csq):
            idx, csq = idx_csq
            info = self._target_info.get(csq)
            if info and info.get("open"):
                open_dt = _parse_open_dt(info["open"])
                is_past = (open_dt is not None) and (open_dt < now)
                if is_past:
                    return (2, info["open"], idx)
                if info.get("title"):
                    return (0, info["open"], idx)
                return (1, info["open"], idx)
            return (3, "", idx)

        sorted_csqs = [csq for _, csq in sorted(enumerate(target_csqs), key=sort_key)]
        if sorted_csqs == target_csqs:
            return  

        self.txt_target.edit_separator()
        self.txt_target.delete("1.0", tk.END)
        self.txt_target.insert("1.0", "\n".join(sorted_csqs))
        self.txt_target.edit_separator()
        self._refresh_target_info_panel()

    # ── 설정 복원/저장 ────────────────────────────────────────────────────────

    def _restore_settings(self):
        s = self._settings
        saved_site = s.get("site", "gt")

        if s.get("remember_email") == "1":
            self.var_email.set(s.get(f"email_{saved_site}", s.get("email", "")))
            self.var_remember_email.set(True)
        if s.get("remember_pw") == "1":
            self.var_pw.set(s.get(f"pw_{saved_site}", s.get("pw", "")))
            self.var_remember_pw.set(True)

        self.var_site.set(saved_site)          
        self.var_headless.set(s.get("headless", "1") == "1")
        self.var_log_save.set(s.get("log_save", "0") == "1")
        self.var_biz_only.set(s.get("biz_only", "0") == "1")

        defaults = {
            "min_etc":       str(getattr(logic, "MIN_PRICE_ETC",  70000) if logic else 70000),
            "min_real":      str(getattr(logic, "MIN_PRICE_REAL", 19700) if logic else 19700),
            "scan_interval": str(getattr(logic, "BASE_REFRESH_INTERVAL", 1) if logic else 1),
            "hot_window":    str(getattr(logic, "HOT_WINDOW_SEC", 120) if logic else 120),
            "max_scroll":    str(getattr(logic, "MAX_SCROLL", 6) if logic else 6),
            "join_timeout":  "0.35",
        }
        for key, var in self._setting_vars.items():
            var.set(s.get(key, defaults[key]))

        if logic:
            perm_path = getattr(logic, "SUCCESS_CSQ_FILE", "success_csq_gt.txt")
            self.txt_perm.insert("1.0", _csq_file_to_text(perm_path))

        self._prev_site    = saved_site
        self._initializing = False
        self._refresh_account_menu()

    def _collect_settings(self) -> dict:
        s = _load_settings()   

        site = self.var_site.get()
        s["remember_email"] = "1" if self.var_remember_email.get() else "0"
        s["remember_pw"]    = "1" if self.var_remember_pw.get() else "0"
        s["site"]           = site
        s["headless"]       = "1" if self.var_headless.get() else "0"
        s["log_save"]       = "1" if self.var_log_save.get() else "0"
        s["biz_only"]       = "1" if self.var_biz_only.get() else "0"
        s["sched_time"]      = self.var_sched_time.get().strip()
        s["stop_sched_time"] = self.var_stop_sched_time.get().strip()

        if self.var_remember_email.get():
            s[f"email_{site}"] = self.var_email.get()
        if self.var_remember_pw.get():
            s[f"pw_{site}"] = self.var_pw.get()

        target_items = list(_text_to_csq_set(self.txt_target.get("1.0", tk.END)))
        s[f"target_csq_{site}"] = ",".join(target_items)

        for key, var in self._setting_vars.items():
            s[key] = var.get()
        return s

    def _apply_logic_settings(self):
        if not logic:
            return
        try:
            logic.MIN_PRICE_ETC         = int(self._setting_vars["min_etc"].get())
            logic.MIN_PRICE_REAL        = int(self._setting_vars["min_real"].get())
            logic.BASE_REFRESH_INTERVAL = float(self._setting_vars["scan_interval"].get())
            logic.HOT_WINDOW_SEC        = int(self._setting_vars["hot_window"].get())
            logic.MAX_SCROLL            = int(self._setting_vars["max_scroll"].get())
            logic.HEADLESS              = self.var_headless.get()
            logic.LOG_SAVE              = self.var_log_save.get()
            
            target_items = list(_text_to_csq_set(self.txt_target.get("1.0", tk.END)))
            logic.TARGET_CSQ = ",".join(target_items)
        except (ValueError, AttributeError) as e:
            if logic:
                logic.log(f"[경고] 설정값 적용 실패 (입력값 확인 필요): {e}")

    # ── 로그 출력 ─────────────────────────────────────────────────────────────

    def _gui_log(self, msg: str):
        import gt_logic as _l
        line = f"[{_l.ts_ms()}]{_l.site_log_tag()} / {msg}\n"
        _l._append_loop_log_line(line.rstrip())
        
        if msg.startswith("[타겟]"):
            self._parse_target_info(msg)
        if msg.startswith("[완료] scan 종료"):
            self.after(0, self._sort_target_by_time)
        if "[결과]" in msg and self._results_win_txt is not None:
            self.after(0, self._append_result_line, line)
        if msg.startswith("[스킵]"):
            if not self.var_show_skip.get():
                return
        if msg.startswith("[진행]"):
            if self.var_brief.get():
                return
        if self.var_only_done.get() and not msg.startswith("[완료]"):
            return
        self.after(0, self._append_log, line)

    def _append_log(self, line: str):
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.insert(tk.END, line)
        if self._log_auto_scroll:
            self.txt_log.see(tk.END)
        self.txt_log.configure(state=tk.DISABLED)

    def _on_log_scroll(self, event=None):
        self.after(50, self._check_log_scroll_position)

    def _on_log_scrollbar_release(self, event=None):
        self.after(50, self._check_log_scroll_position)

    def _check_log_scroll_position(self):
        try:
            pos = self.txt_log.yview()
            if pos[1] >= 0.999:
                self._log_auto_scroll = True
            else:
                self._log_auto_scroll = False
        except Exception:
            pass

    def _clear_log(self):
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.configure(state=tk.DISABLED)

    def _append_result_line(self, line: str):
        if self._results_win_txt is None:
            return
        try:
            self._results_win_txt.insert(tk.END, line)
            self._results_win_txt.see(tk.END)
        except tk.TclError:
            self._results_win_txt = None  

    def _show_results_only(self):
        win = tk.Toplevel(self)
        win.title("참여 결과 모음")
        win.geometry("900x500")

        txt = scrolledtext.ScrolledText(win, font=("Consolas", 10), wrap=tk.WORD)
        txt.pack(fill=tk.BOTH, expand=True)

        full_log = self.txt_log.get("1.0", tk.END)
        result_lines = [line for line in full_log.splitlines() if "[결과]" in line]
        if result_lines:
            txt.insert(tk.END, "\n".join(result_lines) + "\n")
        else:
            txt.insert(tk.END, "(아직 결과 로그가 없습니다)\n")
        txt.see(tk.END)

        self._results_win_txt = txt

        def _on_close():
            self._results_win_txt = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _export_log(self):
        from tkinter import filedialog
        from datetime import datetime
        default_name = f"gt_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(
            title="로그 저장",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        try:
            content = self.txt_log.get("1.0", tk.END)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._set_status(f"로그 저장 완료: {path}", "blue")
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    # ⭐ 로직 모듈에서 핑 결과를 전달받을 콜백 함수 추가
    def _set_ping(self, ping_ms: float):
        # 핑 수치에 따라 색상 변경 (초록: 매우 빠름, 주황: 보통, 빨강: 지연)
        color = "#1565c0"  # 기본 파란색
        if ping_ms < 30: color = "#2e7d32"
        elif ping_ms > 80: color = "#d32f2f"
        
        self.lbl_ping.config(text=f"서버 핑: {ping_ms:.1f} ms", fg=color)

    # ── 루프마다 csq 동기화 ───────────────────────────────────────────────────

    def _sync_csq_on_loop(self):
        if not logic:
            return
        self._apply_logic_settings()
        self._refresh_perm_panel()

        target_items = list(_text_to_csq_set(self.txt_target.get("1.0", tk.END)))
        logic.TARGET_CSQ = ",".join(target_items)

    def _refresh_perm_panel(self):
        if not logic:
            return
        perm_path = getattr(logic, "SUCCESS_CSQ_FILE", "success_csq_gt.txt")
        new_text = _csq_file_to_text(perm_path)
        cur_text = self.txt_perm.get("1.0", tk.END).strip()
        if new_text != cur_text:
            self.txt_perm.delete("1.0", tk.END)
            self.txt_perm.insert("1.0", new_text)

    # ── 버튼 핸들러 ──────────────────────────────────────────────────────────

    def _on_perm_focusout(self, _event=None):
        if not logic:
            return
        perm_path = getattr(logic, "SUCCESS_CSQ_FILE", "success_csq_gt.txt")
        _write_csq_file(perm_path, self.txt_perm.get("1.0", tk.END))

    def _clear_perm(self):
        if not messagebox.askyesno("초기화 확인", "영구저장 CSQ를 모두 지우시겠습니까?\n(파일도 함께 초기화됩니다)"):
            return
        self.txt_perm.delete("1.0", tk.END)
        if logic:
            perm_path = getattr(logic, "SUCCESS_CSQ_FILE", "success_csq_gt.txt")
            _write_csq_file(perm_path, "")

    # ── 계정 관리 ─────────────────────────────────────────────────────────────

    def _load_accounts(self, site: str) -> list:
        s = _load_settings()
        result = []
        i = 0
        while True:
            val = s.get(f"acct_{site}_{i}", "")
            if not val:
                break
            if "::::" in val:
                email, pw = val.split("::::", 1)
                result.append((email.strip(), pw))
            i += 1
        return result

    def _save_accounts(self, site: str, accounts: list):
        s = _load_settings()
        keys_to_del = [k for k in s if k.startswith(f"acct_{site}_")]
        for k in keys_to_del:
            del s[k]
        for i, (email, pw) in enumerate(accounts):
            s[f"acct_{site}_{i}"] = f"{email}::::{pw}"
        _save_settings(s)

    def _refresh_account_menu(self):
        site = self.var_site.get()
        self._acct_list = self._load_accounts(site)
        menu = self._acct_menu["menu"]
        menu.delete(0, "end")
        if not self._acct_list:
            self.var_acct.set("(저장된 계정 없음)")
            menu.add_command(label="(저장된 계정 없음)", command=lambda: None)
        else:
            for email, pw in self._acct_list:
                menu.add_command(
                    label=email,
                    command=lambda e=email, p=pw: self._on_account_selected(e, p)
                )
            cur_email = self.var_email.get().strip()
            matched = next((e for e, _ in self._acct_list if e == cur_email), None)
            self.var_acct.set(matched if matched else self._acct_list[0][0])

    def _on_account_selected(self, email: str, pw: str):
        self.var_acct.set(email)
        self.var_email.set(email)
        self.var_pw.set(pw)

    def _add_account(self):
        email = self.var_email.get().strip()
        pw    = self.var_pw.get().strip()
        if not email or not pw:
            messagebox.showwarning("입력 오류", "이메일과 비밀번호를 먼저 입력해 주세요.")
            return
        site     = self.var_site.get()
        accounts = self._load_accounts(site)
        for i, (e, _) in enumerate(accounts):
            if e == email:
                accounts[i] = (email, pw)
                self._save_accounts(site, accounts)
                self._refresh_account_menu()
                messagebox.showinfo("완료", f"비밀번호 업데이트:\n{email}")
                return
        accounts.append((email, pw))
        self._save_accounts(site, accounts)
        self._refresh_account_menu()
        self.var_acct.set(email)
        messagebox.showinfo("완료", f"계정 추가:\n{email}")

    def _del_account(self):
        selected = self.var_acct.get()
        if not selected or selected == "(저장된 계정 없음)":
            messagebox.showwarning("선택 오류", "삭제할 계정을 선택해 주세요.")
            return
        if not messagebox.askyesno("삭제 확인", f"계정을 삭제하시겠습니까?\n{selected}"):
            return
        site     = self.var_site.get()
        accounts = [(e, p) for e, p in self._load_accounts(site) if e != selected]
        self._save_accounts(site, accounts)
        self._refresh_account_menu()
        if accounts:
            self._on_account_selected(accounts[0][0], accounts[0][1])
        else:
            self.var_email.set("")
            self.var_pw.set("")

    def _on_log_save_changed(self):
        if logic:
            logic.LOG_SAVE = self.var_log_save.get()

    def _on_site_changed(self, *_):
        if not logic:
            return
        new_site = self.var_site.get()
        settings = _load_settings()

        if not self._initializing and new_site != self._prev_site:
            if self.var_remember_email.get():
                settings[f"email_{self._prev_site}"] = self.var_email.get()
            if self.var_remember_pw.get():
                settings[f"pw_{self._prev_site}"]    = self.var_pw.get()

            prev_target_items = list(_text_to_csq_set(self.txt_target.get("1.0", tk.END)))
            settings[f"target_csq_{self._prev_site}"] = ",".join(prev_target_items)
            _save_settings(settings)

            self.var_email.set(settings.get(f"email_{new_site}", ""))
            self.var_pw.set(settings.get(f"pw_{new_site}", ""))

            self._prev_site = new_site

        target_saved = settings.get(f"target_csq_{new_site}", settings.get("target_csq", ""))
        self.txt_target.delete("1.0", tk.END)
        if target_saved:
            items = [x.strip() for x in target_saved.split(",") if x.strip()]
            self.txt_target.insert("1.0", "\n".join(items))
        self._target_info = {}  
        self._refresh_target_info_panel()

        self._refresh_account_menu()

        try:
            logic.apply_site_config(new_site)
        except Exception:
            return
        perm_path = getattr(logic, "SUCCESS_CSQ_FILE", "success_csq_gt.txt")
        self.txt_perm.delete("1.0", tk.END)
        self.txt_perm.insert("1.0", _csq_file_to_text(perm_path))

    # ── 예약 시작 ─────────────────────────────────────────────────────────────

    def _toggle_schedule(self):
        if self._sched_armed:
            self._sched_armed  = False
            self._sched_target = None
            self.ent_sched.config(state=tk.NORMAL)
            self.btn_sched.config(text="예약")
            return

        if self._running:
            messagebox.showwarning("예약 불가", "이미 실행 중입니다.")
            return

        raw = self.var_sched_time.get().strip()
        target = self._parse_sched_time(raw)
        if target is None:
            messagebox.showwarning(
                "입력 오류",
                "시간 형식이 올바르지 않습니다.\n"
                "예: 10:00:00 (오늘/내일)\n"
                "    07/25 10:00:00 (날짜 지정)\n"
                "    2026-07-25 10:00:00 (연도까지 지정)"
            )
            return

        email = self.var_email.get().strip()
        pw    = self.var_pw.get().strip()
        if not email or not pw:
            messagebox.showwarning("입력 오류", "예약 전에 이메일과 비밀번호를 입력해 주세요.")
            return

        self._sched_target = target
        self._sched_armed  = True
        self.ent_sched.config(state=tk.DISABLED)
        self.btn_sched.config(text="예약 취소")
        self._tick_schedule()

    def _parse_sched_time(self, raw: str):
        raw = raw.strip()
        now = datetime.now()

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass

        for fmt in ("%m/%d %H:%M:%S", "%m/%d %H:%M"):
            try:
                t = datetime.strptime(raw, fmt).replace(year=now.year)
                if t <= now:
                    t = t.replace(year=now.year + 1)
                return t
            except ValueError:
                pass

        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                t = datetime.strptime(raw, fmt).time()
                target = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                return target
            except ValueError:
                pass

        return None

    def _tick_schedule(self):
        if not self._sched_armed or self._sched_target is None:
            return
        remain = (self._sched_target - datetime.now()).total_seconds()
        if remain <= 0:
            self._sched_armed  = False
            self.ent_sched.config(state=tk.NORMAL)
            self.btn_sched.config(text="예약")
            self._start()
            return
        self.after(1000, self._tick_schedule)

    # ── 예약 종료 ─────────────────────────────────────────────────────────────

    def _toggle_stop_schedule(self):
        if self._stop_sched_armed:
            self._stop_sched_armed  = False
            self._stop_sched_target = None
            self.ent_stop_sched.config(state=tk.NORMAL)
            self.btn_stop_sched.config(text="예약")
            return

        raw = self.var_stop_sched_time.get().strip()
        target = self._parse_sched_time(raw)
        if target is None:
            messagebox.showwarning(
                "입력 오류",
                "시간 형식이 올바르지 않습니다.\n"
                "예: 18:00:00 (오늘/내일)\n"
                "    07/25 18:00:00 (날짜 지정)\n"
                "    2026-07-25 18:00:00 (연도까지 지정)"
            )
            return

        self._stop_sched_target = target
        self._stop_sched_armed  = True
        self.ent_stop_sched.config(state=tk.DISABLED)
        self.btn_stop_sched.config(text="예약 취소")
        self._tick_stop_schedule()

    def _tick_stop_schedule(self):
        if not self._stop_sched_armed or self._stop_sched_target is None:
            return
        remain = (self._stop_sched_target - datetime.now()).total_seconds()
        if remain <= 0:
            self._stop_sched_armed  = False
            self.ent_stop_sched.config(state=tk.NORMAL)
            self.btn_stop_sched.config(text="예약")
            if self._running:
                self._stop()
            return
        self.after(1000, self._tick_stop_schedule)

    def _start(self):
        if self._running:
            return
        if self._sched_armed:
            self._sched_armed  = False
            self._sched_target = None
            self.ent_sched.config(state=tk.NORMAL)
            self.btn_sched.config(text="예약")
        email = self.var_email.get().strip()
        pw    = self.var_pw.get().strip()
        if not email or not pw:
            messagebox.showwarning("입력 오류", "이메일과 비밀번호를 입력해 주세요.")
            return

        self._apply_logic_settings()
        _save_settings(self._collect_settings())

        if logic:
            try:
                logic.apply_site_config(self.var_site.get())
            except Exception as e:
                messagebox.showerror("사이트 오류", str(e))
                return

        self._running = True
        self._paused  = False
        self._stop_event.clear()
        self._failed_once.clear()

        if logic:
            logic.PAUSED.clear()
            logic.STOP_SIGNAL.clear()  
            # ⭐ 로직 모듈에서 핑 측정이 끝나면 GUI 라벨을 업데이트하도록 콜백 연결
            logic.gui_ping_callback = lambda p: self.after(0, self._set_ping, p)

        self.btn_start.config(state=tk.DISABLED)
        self.btn_pause.config(state=tk.NORMAL)
        self.btn_resume.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self._set_status("실행 중", "green")

        self._thread = threading.Thread(target=self._run_loop,
                                         args=(email, pw), daemon=True)
        self._thread.start()

    def _pause(self):
        if not self._running or self._paused:
            return
        self._paused = True
        if logic:
            logic.PAUSED.set()
        self.btn_pause.config(state=tk.DISABLED)
        self.btn_resume.config(state=tk.NORMAL)
        self._set_status("일시정지", "orange")

    def _resume(self):
        if not self._running or not self._paused:
            return
        self._paused = False
        if logic:
            logic.PAUSED.clear()
        self.btn_pause.config(state=tk.NORMAL)
        self.btn_resume.config(state=tk.DISABLED)
        self._set_status("실행 중", "green")

    def _stop(self):
        if not self._running:
            return
        if self._stop_sched_armed:
            self._stop_sched_armed  = False
            self._stop_sched_target = None
            self.ent_stop_sched.config(state=tk.NORMAL)
            self.btn_stop_sched.config(text="예약")
        self._stop_event.set()
        if logic:
            logic.PAUSED.clear()
            logic.STOP_SIGNAL.set()  
        self._running = False
        self._paused  = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_pause.config(state=tk.DISABLED)
        self.btn_resume.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.DISABLED)
        self._set_status("정지됨", "gray")

    def _reset_storage(self):
        self._failed_once.clear()
        self._set_status("이번실행 목록 초기화됨", "blue")

    def _set_status(self, text: str, color: str = "black"):
        self.lbl_status.config(text=f"상태: {text}", fg=color)

    # ── 메인 감시 루프 (별도 스레드) ─────────────────────────────────────────

    def _run_loop(self, email: str, password: str):
        if not logic:
            self._append_log("[오류] 로직 파일(sum_260609.py)을 찾을 수 없습니다.\n")
            self.after(0, self._stop)
            return

        perm_path = getattr(logic, "SUCCESS_CSQ_FILE", "success_csq_gt.txt")
        if not os.path.exists(perm_path):
            open(perm_path, "w", encoding="utf-8").close()

        driver = None
        try:
            driver = logic.build_driver(headless=self.var_headless.get())
            logic.login_dbg(driver, email, password)

            loop_no = 0
            while not self._stop_event.is_set():
                loop_no += 1
                self.after(0, self._sync_csq_on_loop)
                logic.begin_loop_log(loop_no)
                loop_status = "done"

                try:
                    logic.pause_point()
                    if self._stop_event.is_set():
                        break

                    picked_now, picked_soon = logic.scan_and_pick(driver, self._failed_once)

                    # 오픈 임박
                    if picked_soon is not None:
                        csq = picked_soon["csq"]
                        target_open_dt = picked_soon.get("open_dt")
                        is_target = (picked_soon.get("type") == "TARGET")
                        picked_title_soon = picked_soon.get("title") or ""
                        card_el = logic.get_card_element_by_index(driver, picked_soon["card_index"])
                        if card_el is None:
                            logic.goto_list_page(driver, force_get=True)
                            import time; time.sleep(0.25)
                            loop_status = "soon_card_missing"
                        else:
                            logic.enter_detail_and_wait(driver, card_el, csq, self._failed_once, target_open_dt, is_target=is_target, title=picked_title_soon)
                            import time; time.sleep(0.25)
                            loop_status = f"soon:{csq}"

                    # 즉시 참여
                    elif picked_now is not None:
                        import time
                        csq = picked_now["csq"]
                        picked_title_now = picked_now.get("title") or ""
                        card_el = logic.get_card_element_by_index(driver, picked_now["card_index"])
                        if card_el is None:
                            logic.goto_list_page(driver, force_get=True)
                            time.sleep(0.25)
                            loop_status = "now_card_missing"
                        else:
                            try:
                                _join_timeout = float(self._setting_vars["join_timeout"].get())
                                opened = logic.fast_enter_detail_and_click_join(
                                    driver, card_el, csq, timeout=_join_timeout, popup_retry_after=0.08)
                                if opened == "CLICKED":
                                    logic.log(f"[완료] 즉시참여 JOIN 클릭 성공 (csq={csq})")
                                    logic.process_immediate_join_flow(driver, csq, self._failed_once, campaign_name=picked_title_now)
                                    loop_status = f"now:{csq}"
                                elif isinstance(opened, str) and opened.startswith("CLOSED"):
                                    btn_txt = opened.split(":", 1)[-1] if ":" in opened else opened
                                    logic.log(f"[경고] 버튼 마감/종료 감지 → 이번 실행 스킵: {csq} (텍스트: {btn_txt})")
                                    self._failed_once.add(csq)
                                    logic.goto_list_page(driver, force_get=True)
                                    time.sleep(0.35)
                                    logic.close_site_popups(driver, force=True)
                                    loop_status = f"now_closed:{csq}"
                                elif isinstance(opened, str) and opened.startswith("WAIT"):
                                    btn_txt = opened.split(":", 1)[-1] if ":" in opened else opened
                                    logic.log(f"[대기] 오픈 전 버튼 감지 → 선점 대기 전환: {csq} (텍스트: {btn_txt})")
                                    open_dt = logic.parse_open_dt_from_text(btn_txt)
                                    logic.wait_and_apply_in_detail(driver, csq, self._failed_once, open_dt, campaign_name=picked_title_now)
                                    time.sleep(0.25)
                                    loop_status = f"soon:{csq}"
                                else:
                                    logic.log(f"[경고] 참여 버튼 없음/지연 → 이번 실행 스킵: {csq}")
                                    self._failed_once.add(csq)
                                    logic.goto_list_page(driver, force_get=True)
                                    time.sleep(0.35)
                                    logic.close_site_popups(driver, force=True)
                                    loop_status = f"now_open_failed:{csq}"
                            except logic.StopRequested:
                                raise
                            except Exception as e:
                                logic.log(f"[오류] 즉시 참여 처리 오류: {e}")
                                self._failed_once.add(csq)
                                logic.goto_list_page(driver, force_get=True)
                                time.sleep(0.35)
                                logic.close_site_popups(driver, force=True)
                                loop_status = f"now_error:{csq}"

                    # 후보 없음
                    else:
                        import time
                        loop_status = "none"
                        _interval = logic.BASE_REFRESH_INTERVAL
                        logic.log(f"[대기] 후보 없음 → {_interval}초 대기 (기본 스캔 주기 설정값)")
                        time.sleep(_interval)

                except logic.StopRequested:
                    raise
                except Exception as e:
                    logic.log(f"[오류] 루프 오류: {e}")
                    loop_status = "loop_error"
                finally:
                    logic.end_loop_log(loop_status)
                    self.after(0, self._refresh_perm_panel)
                    self.after(0, self._set_status,
                               f"실행 중 | 상세 선점: {', '.join(sorted(self._failed_once)) or '-'}", "green")

        except logic.StopRequested:
            self.after(0, self._append_log, "[정지] 사용자 종료 요청으로 중단됨\n")
        except Exception as e:
            self.after(0, self._append_log, f"[오류] 치명적 오류: {e}\n")
        finally:
            if driver:
                quit_ok = True
                try:
                    driver.quit()
                except Exception as e:
                    quit_ok = False
                    self.after(0, self._append_log, f"[경고] driver.quit() 실패, 강제 정리 시도: {e}\n")
                if not quit_ok:
                    self._force_kill_driver_processes(driver)
            self.after(0, self._stop)

    def _force_kill_driver_processes(self, driver):
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
                children = parent.children(recursive=True)
                for p in children:
                    try:
                        p.kill()
                    except Exception:
                        pass
                parent.kill()
                self.after(0, self._append_log, f"[정리] 남은 드라이버 프로세스 강제 종료 완료 (pid={pid})\n")
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
                self.after(0, self._append_log, f"[정리] 남은 드라이버 프로세스 강제 종료 시도 (pid={pid})\n")
            except Exception as e:
                self.after(0, self._append_log, f"[경고] 드라이버 프로세스 강제 종료 실패: {e}\n")

    # ── 종료 처리 ─────────────────────────────────────────────────────────────

    def _on_close(self):
        if self._running:
            if not messagebox.askyesno("종료 확인", "감시가 실행 중입니다.\n종료하시겠습니까?"):
                return
        self._sched_armed = False
        self._stop_sched_armed = False
        _save_settings(self._collect_settings())
        if logic:
            perm_path = getattr(logic, "SUCCESS_CSQ_FILE", "success_csq_gt.txt")
            _write_csq_file(perm_path, self.txt_perm.get("1.0", tk.END))
        self._stop()
        self.destroy()

if __name__ == "__main__":
    app = GTApp()
    app.mainloop()
