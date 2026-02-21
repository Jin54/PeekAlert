"""
PeekAlert v1.0
Discord 알림 → 서브모니터 팝업
트레이 아이콘 우클릭 → 설정/종료
"""

import sys, os, subprocess, threading, time, json, queue
import sqlite3, shutil, tempfile
import xml.etree.ElementTree as ET
import ctypes, ctypes.wintypes as wt

# ── 패키지 자동 설치 ──────────────────────────
def _pip(pkg):
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", pkg, "-q"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

print("패키지 확인 중...")
try:    import pystray
except: print("설치 중: pystray"); _pip("pystray"); import pystray
try:    from PIL import Image, ImageDraw
except: print("설치 중: Pillow");  _pip("Pillow");  from PIL import Image, ImageDraw
import tkinter as tk
from tkinter import ttk, colorchooser
print("패키지 OK\n")

# ── 설정 ─────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peekalert_config.json")

DEFAULT_CONFIG = {
    "monitor_index":     1,
    "position":          "bottom_right",
    "margin":            20,
    "offset_x":          0,
    "offset_y":          0,
    "popup_width":       380,
    "popup_height":      90,
    "popup_duration_ms": 5000,
    "max_popups":        3,
    "opacity":           0.95,
    "title_size":        10,
    "body_size":         9,
    "title_color":       "#ffffff",
    "body_color":        "#dcddde",
    "bg_color":          "#36393f",
    "accent_color":      "#5865f2",
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(json.load(f))
                return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ── 메인 스레드 큐 ────────────────────────────
_main_queue = queue.Queue()

def _queue_task(fn):
    _main_queue.put(fn)

# ── ctypes 모니터 ─────────────────────────────
user32 = ctypes.windll.user32

class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize",    wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork",    wt.RECT),
        ("dwFlags",   wt.DWORD),
    ]

_MonitorEnumProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool, wt.HMONITOR, wt.HDC,
    ctypes.POINTER(wt.RECT), wt.LPARAM,
)

def _enum_monitors():
    result = []
    def cb(hMon, hdc, lpRect, data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hMon, ctypes.byref(info))
        r = info.rcWork
        result.append((r.left, r.top, r.right, r.bottom, bool(info.dwFlags & 1)))
        return True
    user32.EnumDisplayMonitors(None, None, _MonitorEnumProc(cb), 0)
    primary = [m for m in result if m[4]]
    others  = [m for m in result if not m[4]]
    return primary + others

def get_work_area(index):
    mons = _enum_monitors()
    if not mons: return (0, 0, 1920, 1040)
    l, t, r, b, _ = mons[min(index, len(mons)-1)]
    return (l, t, r, b)

def count_monitors():
    return len(_enum_monitors())


# ── 팝업 스택 ─────────────────────────────────
# 각 항목: {"id": int, "win": Toplevel, "target_y": int}
# 메인 스레드에서만 접근 (락 불필요)
_popup_stack = []
_pid_counter = [0]
GAP = 8  # 팝업 간 간격

def _base_x_y(cfg):
    """첫 번째 팝업(슬롯0)의 x, y 및 모니터 영역 반환"""
    l, t, r, b = get_work_area(cfg["monitor_index"])
    W = cfg["popup_width"]
    H = cfg["popup_height"]
    m = cfg["margin"]
    p = cfg["position"]
    if   p == "bottom_right": bx, by = r - W - m, b - H - m
    elif p == "bottom_left":  bx, by = l + m,      b - H - m
    elif p == "top_right":    bx, by = r - W - m,  t + m
    elif p == "top_left":     bx, by = l + m,       t + m
    else:                     bx, by = r - W - m,  b - H - m
    return bx + cfg["offset_x"], by + cfg["offset_y"], l, t, r, b

def _target_y_for_slot(slot, cfg):
    """slot 번째 팝업의 목표 y 좌표"""
    _, base_y, l, t, r, b = _base_x_y(cfg)
    H = cfg["popup_height"]
    p = cfg["position"]
    if "top" in p:
        # 위쪽 기준: 아래로 쌓임
        return base_y + slot * (H + GAP)
    else:
        # 아래쪽 기준: 위로 쌓임
        return base_y - slot * (H + GAP)

def _restack(cfg):
    """살아있는 팝업들의 목표 y를 슬롯 번호 기준으로 재계산 후 이동"""
    W = cfg["popup_width"]
    H = cfg["popup_height"]
    base_x, _, l, t, r, b = _base_x_y(cfg)

    for slot, entry in enumerate(_popup_stack):
        new_y = _target_y_for_slot(slot, cfg)
        entry["target_y"] = new_y
        try:
            entry["win"].geometry(f"{W}x{H}+{base_x}+{new_y}")
        except Exception:
            pass

def _remove_popup(pid, cfg):
    """pid에 해당하는 팝업 제거 후 나머지 재정렬"""
    global _popup_stack
    new_stack = []
    for entry in _popup_stack:
        if entry["id"] == pid:
            try: entry["win"].destroy()
            except: pass
        else:
            new_stack.append(entry)
    _popup_stack = new_stack
    _restack(cfg)

def _create_popup(title, body, preview=False):
    """메인 스레드에서 호출"""
    cfg = load_config()
    W, H = cfg["popup_width"], cfg["popup_height"]
    base_x, base_y, l, t, r, b = _base_x_y(cfg)
    p = cfg["position"]

    # 최대 개수 초과 시 가장 오래된 팝업 제거
    while len(_popup_stack) >= cfg["max_popups"]:
        oldest = _popup_stack[0]
        try: oldest["win"].destroy()
        except: pass
        _popup_stack.pop(0)

    # 새 팝업의 슬롯 = 현재 스택 크기
    slot = len(_popup_stack)
    target_y = _target_y_for_slot(slot, cfg)

    # 슬라이드 시작 y (화면 바깥)
    if "top" in p:
        start_y = t - H - 5
    else:
        start_y = b + 5

    pid = _pid_counter[0]
    _pid_counter[0] += 1

    win = tk.Toplevel()
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", cfg["opacity"])
    win.configure(bg=cfg["bg_color"])
    win.geometry(f"{W}x{H}+{base_x}+{start_y}")
    win.update()

    # 레이아웃
    tk.Frame(win, bg=cfg["accent_color"], width=4).pack(side="left", fill="y")
    tk.Label(win, text="  💬", bg=cfg["bg_color"],
             font=("Segoe UI Emoji", cfg["title_size"] + 2)).pack(side="left", padx=(6, 0), pady=6)

    frm = tk.Frame(win, bg=cfg["bg_color"])
    frm.pack(side="left", fill="both", expand=True, padx=8, pady=6)
    tk.Label(frm, text=title,
             bg=cfg["bg_color"], fg=cfg["title_color"],
             font=("Segoe UI", cfg["title_size"], "bold"),
             anchor="w", wraplength=W - 90, justify="left").pack(anchor="w")
    if body:
        tk.Label(frm, text=body,
                 bg=cfg["bg_color"], fg=cfg["body_color"],
                 font=("Segoe UI", cfg["body_size"]),
                 anchor="w", wraplength=W - 90, justify="left").pack(anchor="w")

    # 스택에 등록
    entry = {"id": pid, "win": win, "target_y": target_y}
    _popup_stack.append(entry)

    def close(e=None):
        _remove_popup(pid, load_config())

    win.bind("<Button-1>", close)
    for w in win.winfo_children():
        w.bind("<Button-1>", close)

    # 슬라이드 인 애니메이션
    def slide(i=0):
        if i > 14:
            dur = 2000 if preview else cfg["popup_duration_ms"]
            try: win.after(dur, close)
            except: pass
            return
        cy = int(start_y + (target_y - start_y) * (i / 14))
        try:
            win.geometry(f"{W}x{H}+{base_x}+{cy}")
            win.after(13, slide, i + 1)
        except:
            pass

    slide()


def show_popup(title, body, preview=False):
    _queue_task(lambda: _create_popup(title, body, preview))


# ── DB 폴링 ───────────────────────────────────
DB_PATH = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db"
)

def _read_db(query):
    tmp = tempfile.mktemp(suffix=".db")
    try:
        shutil.copy2(DB_PATH, tmp)
        con = sqlite3.connect(tmp)
        rows = con.execute(query).fetchall()
        con.close()
        return rows
    finally:
        try: os.unlink(tmp)
        except: pass

def get_discord_ids():
    try:
        rows = _read_db("SELECT * FROM NotificationHandler")
        return {row[0] for row in rows
                if "discord" in " ".join(str(v).lower() for v in row if v)}
    except: return set()

def poll_notifications():
    if not os.path.exists(DB_PATH):
        print(f"[오류] 알림 DB 없음: {DB_PATH}"); return

    discord_ids = get_discord_ids()
    print(f"[OK] Discord HandlerId: {discord_ids if discord_ids else '자동감지 실패→전체'}")
    print(f"[OK] 감지 시작! 트레이 우클릭 → 설정/종료\n")

    seen_ids = set()
    try:
        for (rid,) in _read_db("SELECT Id FROM Notification"): seen_ids.add(rid)
    except: pass

    tick = 0
    while True:
        time.sleep(1.0)
        tick += 1
        if tick >= 30:
            tick = 0
            new = get_discord_ids()
            if new != discord_ids:
                discord_ids.clear(); discord_ids.update(new)
                print(f"[갱신] Discord HandlerId: {discord_ids}")

        try: rows = _read_db("SELECT Id, HandlerId, Payload FROM Notification ORDER BY Id DESC LIMIT 30")
        except: continue

        for rid, handler_id, payload in rows:
            if rid in seen_ids: continue
            seen_ids.add(rid)
            if not payload: continue
            is_discord = (handler_id in discord_ids) if discord_ids else ("discord" in str(payload).lower())
            if not is_discord: continue
            try:
                ps = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
                texts = [e.text for e in ET.fromstring(ps).iter("text") if e.text]
                clean = lambda s: s.replace("\u2068","").replace("\u2069","").strip()
                title = clean(texts[0]) if texts else "Discord"
                body  = clean(texts[1]) if len(texts) > 1 else ""
                print(f"  [알림] {title}: {body}")
                show_popup(title, body)
            except Exception as e:
                print(f"  [파싱오류] {e}")


# ── 설정 창 ───────────────────────────────────
def _create_settings_window():
    cfg = load_config()
    mon_count = count_monitors()

    win = tk.Toplevel()
    win.title("PeekAlert 설정")
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.configure(bg="#2f3136", padx=16, pady=12)

    style = ttk.Style(win)
    style.theme_use("clam")
    for w in ("TLabel", "TFrame"):
        style.configure(w, background="#2f3136", foreground="#ffffff", font=("Segoe UI", 9))
    style.configure("TLabelframe",       background="#2f3136")
    style.configure("TLabelframe.Label", background="#2f3136", foreground="#aaaaaa", font=("Segoe UI", 9, "bold"))
    style.configure("TButton",   font=("Segoe UI", 9), padding=4)
    style.configure("TCombobox", fieldbackground="#40444b", foreground="#ffffff")
    style.configure("Horizontal.TScale", background="#2f3136", troughcolor="#40444b")

    def section(title):
        lf = ttk.LabelFrame(win, text=title, padding=(10, 5))
        lf.pack(fill="x", pady=(0, 8))
        return lf

    def lrow(parent, label):
        f = ttk.Frame(parent); f.pack(fill="x", pady=2)
        ttk.Label(f, text=label, width=16, anchor="w").pack(side="left")
        return f

    def slider_row(parent, label, var, from_, to_, fmt):
        f = lrow(parent, label)
        lbl = ttk.Label(f, text=fmt(var.get()), width=6)
        ttk.Scale(f, from_=from_, to=to_, variable=var, orient="horizontal", length=140,
                  command=lambda v: lbl.config(text=fmt(float(v)))).pack(side="left")
        lbl.pack(side="left", padx=4)

    def color_row(parent, label, color_var):
        f = lrow(parent, label)
        prev = tk.Label(f, bg=color_var.get(), width=3, relief="solid", bd=1)
        prev.pack(side="left", padx=(0, 6))
        hex_lbl = ttk.Label(f, text=color_var.get(), width=8)
        hex_lbl.pack(side="left", padx=(0, 6))
        def pick():
            res = colorchooser.askcolor(color=color_var.get(), title=label, parent=win)
            if res and res[1]:
                color_var.set(res[1])
                prev.config(bg=res[1])
                hex_lbl.config(text=res[1])
        ttk.Button(f, text="선택", command=pick).pack(side="left")

    # 위치
    s1 = section("📍 팝업 위치")
    f = lrow(s1, "모니터")
    mon_cb = ttk.Combobox(f, values=[f"모니터 {i} {'(주)' if i==0 else '(서브)'}" for i in range(mon_count)],
                          state="readonly", width=18)
    mon_cb.current(min(cfg["monitor_index"], mon_count-1)); mon_cb.pack(side="left")

    f = lrow(s1, "위치")
    pos_map = {"우하단":"bottom_right","좌하단":"bottom_left","우상단":"top_right","좌상단":"top_left"}
    rev_map = {v:k for k,v in pos_map.items()}
    pos_cb = ttk.Combobox(f, values=list(pos_map.keys()), state="readonly", width=10)
    pos_cb.set(rev_map.get(cfg["position"],"우하단")); pos_cb.pack(side="left")

    margin_var = tk.IntVar(value=cfg["margin"])
    slider_row(s1, "여백 (px)", margin_var, 0, 100, lambda v: f"{int(v)}px")

    f = lrow(s1, "X / Y 오프셋")
    offx_var = tk.IntVar(value=cfg["offset_x"])
    offy_var = tk.IntVar(value=cfg["offset_y"])
    ttk.Spinbox(f, from_=-500, to=500, textvariable=offx_var, width=6).pack(side="left")
    ttk.Label(f, text=" / ").pack(side="left")
    ttk.Spinbox(f, from_=-500, to=500, textvariable=offy_var, width=6).pack(side="left")

    # 크기
    s2 = section("📐 팝업 크기")
    w_var = tk.IntVar(value=cfg["popup_width"])
    h_var = tk.IntVar(value=cfg["popup_height"])
    slider_row(s2, "너비 (px)", w_var, 200, 700, lambda v: f"{int(v)}px")
    slider_row(s2, "높이 (px)", h_var, 60,  200, lambda v: f"{int(v)}px")

    # 표시
    s3 = section("⏱ 표시 / 개수 / 투명도")
    dur_var = tk.IntVar(value=cfg["popup_duration_ms"])
    opa_var = tk.DoubleVar(value=cfg["opacity"])
    max_var = tk.IntVar(value=cfg["max_popups"])
    slider_row(s3, "표시 시간", dur_var, 1000, 15000, lambda v: f"{int(v)//1000}초")
    slider_row(s3, "투명도",    opa_var, 0.3,  1.0,   lambda v: f"{int(float(v)*100)}%")
    f = lrow(s3, "최대 팝업 수")
    ttk.Spinbox(f, from_=1, to=10, textvariable=max_var, width=4).pack(side="left")
    ttk.Label(f, text="  개").pack(side="left")

    # 텍스트
    s4 = section("🔤 텍스트")
    ts_var = tk.IntVar(value=cfg["title_size"])
    bs_var = tk.IntVar(value=cfg["body_size"])
    title_color_var = tk.StringVar(value=cfg["title_color"])
    body_color_var  = tk.StringVar(value=cfg["body_color"])
    slider_row(s4, "제목 크기", ts_var, 7, 20, lambda v: f"{int(v)}pt")
    slider_row(s4, "본문 크기", bs_var, 7, 20, lambda v: f"{int(v)}pt")
    color_row(s4, "제목 색상", title_color_var)
    color_row(s4, "본문 색상", body_color_var)

    # 배경
    s5 = section("🎨 배경")
    bg_var     = tk.StringVar(value=cfg["bg_color"])
    accent_var = tk.StringVar(value=cfg["accent_color"])
    color_row(s5, "배경색",    bg_var)
    color_row(s5, "강조색(바)", accent_var)

    def get_values():
        return {
            **load_config(),
            "monitor_index":     mon_cb.current(),
            "position":          pos_map[pos_cb.get()],
            "margin":            margin_var.get(),
            "offset_x":          offx_var.get(),
            "offset_y":          offy_var.get(),
            "popup_width":       w_var.get(),
            "popup_height":      h_var.get(),
            "popup_duration_ms": dur_var.get(),
            "max_popups":        max_var.get(),
            "opacity":           round(opa_var.get(), 2),
            "title_size":        ts_var.get(),
            "body_size":         bs_var.get(),
            "title_color":       title_color_var.get(),
            "body_color":        body_color_var.get(),
            "bg_color":          bg_var.get(),
            "accent_color":      accent_var.get(),
        }

    def do_preview():
        save_config(get_values())
        show_popup("미리보기 💬", "이 설정으로 팝업이 표시됩니다!", preview=True)

    def do_save():
        save_config(get_values())
        win.destroy()

    bf = ttk.Frame(win); bf.pack(fill="x", pady=(6, 0))
    ttk.Button(bf, text="미리보기",     command=do_preview).pack(side="left", padx=(0, 6))
    ttk.Button(bf, text="저장 후 닫기", command=do_save).pack(side="left")
    ttk.Button(bf, text="취소",         command=win.destroy).pack(side="right")

def open_settings():
    _queue_task(_create_settings_window)


# ── 트레이 아이콘 ─────────────────────────────
def _make_icon():
    img = Image.new("RGBA", (64, 64), (0,0,0,0))
    d = ImageDraw.Draw(img)
    d.ellipse([2,2,62,62], fill="#5865f2")
    d.ellipse([20,20,44,44], fill="white")
    d.rectangle([20,20,32,44], fill="#5865f2")
    return img

def run_tray():
    icon = pystray.Icon(
        "PeekAlert", _make_icon(), "PeekAlert",
        pystray.Menu(
            pystray.MenuItem("테스트 팝업", lambda i, item: show_popup("친구#1234", "야 게임하자! 들어와~")),
            pystray.MenuItem("설정",        lambda i, item: open_settings()),
            pystray.MenuItem("종료",        lambda i, item: (i.stop(), os._exit(0))),
        )
    )
    threading.Thread(target=icon.run, daemon=True).start()


# ── 메인 루프 ─────────────────────────────────
if __name__ == "__main__":
    print("=" * 48)
    print("  PeekAlert v1.0  - Discord → 서브모니터")
    print("=" * 48)

    threading.Thread(target=poll_notifications, daemon=True).start()
    run_tray()

    root = tk.Tk()
    root.withdraw()

    while True:
        try:
            fn = _main_queue.get_nowait()
            fn()
        except queue.Empty:
            pass
        try:
            root.update()
        except Exception:
            break
        time.sleep(0.02)
