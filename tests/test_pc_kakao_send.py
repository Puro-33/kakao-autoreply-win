"""pc_kakao_send.py 테스트. 실제 카톡에는 절대 보내지 않는다.

- 가짜 카카오톡 창(이 파일을 --fake-kakao로 다시 실행한 별도 python 프로세스, ctypes)을 띄워
  같은 창 구조(EVA_Window_Dblclk '카카오톡' > EVA_ChildWindow > EVA_Window x2 > Edit,
  채팅창 EVA_Window_Dblclk '방 이름' > RICHEDIT50W)에 대고 시험한다.
- 모든 함수에 pids={가짜 pid}를 넘긴다. set_text·press_enter는 가짜 pid 창이 아니면 예외를 내도록 감싼다.
- send()는 dry-run으로만 부른다. Enter 경로는 deliver()를 가짜 입력칸 hwnd에 직접 불러 시험한다.
- PC 카카오톡이 켜져 있으면 읽기 전용 probe만 한다(글 넣기·Enter 없음). 꺼져 있으면 건너뜀.

실행: py -3.12 -I tests\\test_pc_kakao_send.py
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")


# ======================================================================== 가짜 카카오톡 (별도 프로세스)
def run_fake(cfg: dict) -> None:
    import ctypes
    import ctypes.wintypes as W

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # 실제 카톡처럼 DPI 인식(물리 좌표로 클릭 받음)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.LoadLibraryW.argtypes = [W.LPCWSTR]
    k.LoadLibraryW.restype = W.HMODULE
    k.GetModuleHandleW.argtypes = [W.LPCWSTR]
    k.GetModuleHandleW.restype = W.HMODULE
    if not k.LoadLibraryW("Msftedit.dll"):  # RICHEDIT50W
        raise SystemExit("Msftedit.dll 로드 실패")
    LRESULT = W.LPARAM
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.DefWindowProcW.restype = LRESULT
    u.CallWindowProcW.argtypes = [ctypes.c_void_p, W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    u.CallWindowProcW.restype = LRESULT
    u.SetWindowLongPtrW.argtypes = [W.HWND, ctypes.c_int, ctypes.c_void_p]
    u.SetWindowLongPtrW.restype = ctypes.c_void_p
    u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    u.CreateWindowExW.restype = W.HWND
    u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    u.GetWindowTextLengthW.argtypes = [W.HWND]
    u.SetWindowTextW.argtypes = [W.HWND, W.LPCWSTR]
    u.DestroyWindow.argtypes = [W.HWND]
    u.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
    u.GetKeyState.argtypes = [ctypes.c_int]
    u.GetKeyState.restype = ctypes.c_short
    u.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
    u.TranslateMessage.argtypes = [ctypes.POINTER(W.MSG)]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                    ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
                    ("lpszClassName", W.LPCWSTR)]

    WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD = 0x00CF0000, 0x10000000, 0x40000000
    ES_MULTILINE, ES_WANTRETURN, ES_AUTOHSCROLL = 0x0004, 0x1000, 0x0080
    WM_CLOSE, WM_DESTROY, WM_KEYDOWN, WM_CHAR = 0x0010, 0x0002, 0x0100, 0x0102
    hinst = k.GetModuleHandleW(None)
    keep = []  # 콜백 참조 유지
    state = {"main": None, "chats": {}, "old": {}}
    log_f = open(cfg["log"], "a", encoding="utf-8")

    def log(**ev):
        log_f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        log_f.flush()

    def text_of(h):
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        return b.value

    def top_proc(h, msg, wp, lp):
        if msg == 0x0202:   # WM_LBUTTONUP: 오른쪽 아래 전송 버튼 자리면 보냄(실제 카톡처럼 — 11-12부터 Enter 대신)
            rc = W.RECT(); u.GetClientRect(h, ctypes.byref(rc))
            x, y = lp & 0xFFFF, (lp >> 16) & 0xFFFF
            hit = next(((n, e) for n, (ch, e) in state["chats"].items() if ch == h), None)
            s = (u.GetDpiForWindow(h) or 96) / 96.0
            if hit and rc.right - 60 * s <= x <= rc.right and rc.bottom - 45 * s <= y <= rc.bottom and len(hit[1]) == 1:
                chat_enter(hit[1][0])
            return 0
        if msg == WM_CLOSE:
            for name, (ch, _) in list(state["chats"].items()):
                if ch == h:
                    del state["chats"][name]
                    log(event="closed", room=name)
            u.DestroyWindow(h)
            return 0
        if msg == WM_DESTROY and h == state["main"]:
            u.PostQuitMessage(0)
            return 0
        return u.DefWindowProcW(h, msg, wp, lp)

    def plain_proc(h, msg, wp, lp):
        return u.DefWindowProcW(h, msg, wp, lp)

    for cls, proc in (("EVA_Window_Dblclk", top_proc), ("EVA_ChildWindow", plain_proc), ("EVA_Window", plain_proc)):
        cb = WNDPROC(proc)
        keep.append(cb)
        wc = WNDCLASSW(lpfnWndProc=cb, hInstance=hinst, lpszClassName=cls)
        if not u.RegisterClassW(ctypes.byref(wc)):
            raise SystemExit("RegisterClassW 실패 " + cls)

    def subclass(h, handler):
        def proc(hh, msg, wp, lp):
            if msg == WM_KEYDOWN and wp == 0x0D and not (u.GetKeyState(0x10) & 0x8000):
                handler(hh)
                return 0
            if msg == WM_CHAR and wp == 0x0D:  # TranslateMessage가 만든 '\r' 삼킴(카톡처럼)
                return 0
            return u.CallWindowProcW(state["old"][hh], hh, msg, wp, lp)
        cb = WNDPROC(proc)
        keep.append(cb)
        state["old"][h] = u.SetWindowLongPtrW(h, -4, ctypes.cast(cb, ctypes.c_void_p))  # GWLP_WNDPROC

    def chat_enter(edit):
        room = next(n for n, (_, e) in state["chats"].items() if edit in e)
        t = text_of(edit)
        if t.strip():
            log(event="sent", room=room, text=t)
            u.SetWindowTextW(edit, "")

    def make_chat(name):
        h = u.CreateWindowExW(0, "EVA_Window_Dblclk", name, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                              100, 100, 360, 480, None, None, hinst, None)
        edits = []
        for i in range(2 if name in cfg.get("two_inputs", []) else 1):
            e = u.CreateWindowExW(0, "RICHEDIT50W", "", WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_WANTRETURN,
                                  5, 360 - i * 60, 330, 50, h, None, hinst, None)
            subclass(e, chat_enter)
            edits.append(e)
        state["chats"][name] = (h, edits)
        if name in cfg.get("minimized", []):
            u.ShowWindow(h, 6)  # SW_MINIMIZE
        return h

    def search_enter(edit):
        q = text_of(edit)
        hits = [r for r in cfg.get("searchable", []) if q and q in r]
        if hits and hits[0] not in state["chats"]:
            make_chat(hits[0])
            log(event="opened", room=hits[0], query=q)

    main = u.CreateWindowExW(0, "EVA_Window_Dblclk", "카카오톡", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                             20, 20, 380, 600, None, None, hinst, None)
    state["main"] = main
    cw = u.CreateWindowExW(0, "EVA_ChildWindow", "", WS_CHILD | WS_VISIBLE, 0, 0, 360, 560, main, None, hinst, None)
    friends = u.CreateWindowExW(0, "EVA_Window", "", WS_CHILD, 0, 0, 360, 560, cw, None, hinst, None)
    chats_tab = u.CreateWindowExW(0, "EVA_Window", "", WS_CHILD | WS_VISIBLE, 0, 0, 360, 560, cw, None, hinst, None)
    u.CreateWindowExW(0, "Edit", "", WS_CHILD | ES_AUTOHSCROLL, 5, 5, 300, 24, friends, None, hinst, None)
    search = u.CreateWindowExW(0, "Edit", "", WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL, 5, 5, 300, 24,
                               chats_tab, None, hinst, None)
    subclass(search, search_enter)
    for name in cfg.get("open", []):
        make_chat(name)
    print(json.dumps({"pid": os.getpid(), "main": main, "search": search}), flush=True)
    msg = W.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))


if len(sys.argv) >= 3 and sys.argv[1] == "--fake-kakao":
    run_fake(json.loads(sys.argv[2]))
    raise SystemExit(0)


# ======================================================================== 테스트 본체
sys.path.insert(0, str(ROOT))
import pc_kakao_send as M  # noqa: E402

ok = True
cnt = 0


def check(name, cond, extra=""):
    global ok, cnt
    cnt += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))


class Fake:
    def __init__(self, **cfg):
        fd, self.log = tempfile.mkstemp(prefix="fake_kakao_", suffix=".jsonl")
        os.close(fd)
        cfg["log"] = self.log
        self.proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--fake-kakao",
                                      json.dumps(cfg, ensure_ascii=False)],
                                     stdout=subprocess.PIPE, text=True, encoding="utf-8")
        info = json.loads(self.proc.stdout.readline())
        self.pid, self.main, self.search = info["pid"], info["main"], info["search"]
        self.pids = {self.pid}
        FAKE_PIDS.add(self.pid)

    def events(self, kind=None):
        with open(self.log, encoding="utf-8") as f:
            evs = [json.loads(x) for x in f if x.strip()]
        return [e for e in evs if kind is None or e["event"] == kind]

    def close(self):
        M.user32.PostMessageW(self.main, M.WM_CLOSE, 0, 0)
        try:
            self.proc.wait(3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        FAKE_PIDS.discard(self.pid)
        os.remove(self.log)


# ---- 안전 가드: 글 넣기·Enter는 가짜 프로세스 창에만
FAKE_PIDS: set[int] = set()
_real_set, _real_enter = M.set_text, M.press_enter


def _guard(hwnd):
    pid = M.window_pid(hwnd)
    if pid not in FAKE_PIDS:
        raise AssertionError(f"가짜 창이 아닌 hwnd {hwnd} (pid {pid}, {M.process_exe(pid)})에 쓰려 함 - 중단")


def guarded_set(hwnd, text):
    _guard(hwnd)
    return _real_set(hwnd, text)


def guarded_enter(hwnd):
    _guard(hwnd)
    return _real_enter(hwnd)


M.set_text, M.press_enter = guarded_set, guarded_enter

# 이 파일은 send()에 실제 전송 플래그를 넘기지 않는다(문자열로 확인)
check("테스트 소스에 실제 전송 호출 없음", ("send=" + "True") not in Path(__file__).read_text(encoding="utf-8"))

ROOM = "__가짜방_테스트__"
MINI = "__최소화방__"
TWO = "__입력칸둘__"
fake = Fake(open=[ROOM, MINI, TWO], minimized=[MINI], two_inputs=[TWO],
            searchable=["__검색방__", "가족여행", "가족"])
try:
    P = fake.pids
    # --- 창 찾기
    check("기본 범위(KakaoTalk.exe만)에서는 가짜 창 안 잡힘", M.find_chat_window(ROOM) is None)
    h = M.find_chat_window(ROOM, P)
    check("find_chat_window 찾음", bool(h))
    check("채팅창 클래스 EVA_Window_Dblclk", h and M.class_name(h) == "EVA_Window_Dblclk")
    check("메인 '카카오톡' 창은 채팅창으로 안 잡힘", M.find_chat_window("카카오톡", P) is None)
    check("없는 방 None", M.find_chat_window("__없는방__", P) is None)
    inputs = M.input_controls(h)
    check("입력칸 RICHEDIT50W 1개", len(inputs) == 1 and M.class_name(inputs[0]) == "RICHEDIT50W")
    inp = inputs[0]
    main = M.find_main_window(P)
    se, how = M.find_search_edit(main)
    check("메인 창·검색칸(채팅 탭) 찾음", main == fake.main and se == fake.search and how.startswith("chain"), how)

    # --- dry-run send
    r = M.send(ROOM, "안녕하세요 😀")
    check("dry-run 기본(범위 밖이라 창 없음)", not r["ok"] and r["dry_run"])
    r = M.send(ROOM, "안녕하세요 😀", pids=P)
    check("dry-run ok·전송 없음", r["ok"] and r["dry_run"] and not fake.events("sent"), r)
    check("dry-run은 입력칸 안 건드림", M.get_text(inp) == "")
    r = M.send(ROOM, "첫 줄\n둘째 줄", pids=P, probe_input=True)
    check("probe_input: 넣고 읽고 되돌림, Enter 없음", r["ok"] and r.get("probe_ok") and M.get_text(inp) == ""
          and not fake.events("sent"), r)

    # --- 거부 경로
    M.set_text(inp, "OWNER이 쓰던 글")
    r = M.send(ROOM, "봇 답", pids=P, probe_input=True)
    check("초안 있으면 거부·초안 보존", not r["ok"] and "쓰던 글" in r["error"] and M.get_text(inp) == "OWNER이 쓰던 글", r)
    r = M.send(ROOM, "봇 답", pids=P, overwrite_draft=True, probe_input=True)
    check("overwrite_draft dry-run probe 후 초안 복원", r["ok"] and r.get("probe_ok") and M.get_text(inp) == "OWNER이 쓰던 글", r)
    M.set_text(inp, "")
    r = M.send(ROOM, "댓글", pids=P, thread_id="123")
    check("thread_id 거부(본문으로 새지 않음)", not r["ok"] and "스레드" in r["error"])
    check("빈 글 거부", not M.send(ROOM, "  \n ", pids=P)["ok"])
    r = M.send(TWO, "x", pids=P)
    check("입력칸 2개면 거부", not r["ok"] and "2개" in r["error"], r)
    r = M.send("__안열린방__", "x", pids=P)
    check("창 없고 auto_open=False면 거부", not r["ok"] and "열려 있지" in r["error"])
    r = M.send("__검색방__", "x", pids=P, auto_open=True)
    check("auto_open dry-run: 계획만, 방 안 열림", r["ok"] and r["dry_run"] and not fake.events("opened")
          and M.find_chat_window("__검색방__", P) is None, r)

    # --- Enter 경로 (가짜 입력칸에만, deliver 직접)
    d = M.deliver(inp, "안녕하세요, 테스트입니다 😀")
    sent = fake.events("sent")
    check("deliver 한글·이모지 1건 전송·확인", d["ok"] and d["confirmed"] and len(sent) == 1
          and sent[0]["text"] == "안녕하세요, 테스트입니다 😀" and sent[0]["room"] == ROOM, (d, sent))
    multi = "첫 줄\n둘째 줄\r\n\n넷째 줄 끝"
    d = M.deliver(inp, multi)
    sent = fake.events("sent")
    got = sent[-1]["text"].replace("\r\n", "\n").replace("\r", "\n") if sent else None
    check("여러 줄 한 번에 1건(줄바꿈 보존)", d["ok"] and d["confirmed"] and len(sent) == 2
          and got == "첫 줄\n둘째 줄\n\n넷째 줄 끝", repr(sent[-1]["text"] if sent else None))
    hm = M.find_chat_window(MINI, P)
    check("최소화 창도 찾음", bool(hm) and bool(M.user32.IsIconic(hm)))
    d = M.deliver(M.input_controls(hm)[0], "최소화 창에 전송")
    check("최소화 창에도 전송됨(가짜)", d["confirmed"] and fake.events("sent")[-1]["room"] == MINI)

    # --- open_chat
    r = M.open_chat("__검색방__", pids=P)
    check("open_chat dry-run: 검색칸 안 건드림", r["ok"] and r.get("dry_run") and M.get_text(fake.search) == ""
          and not fake.events("opened"), r)
    r = M.open_chat(ROOM, pids=P)
    check("open_chat 이미 열린 방", r["ok"] and r.get("already_open"))
    r = M.open_chat("__검색방__", execute=True, pids=P)
    check("open_chat 실행(가짜): 열림·검색칸 비움", r["ok"] and r["hwnd"] == M.find_chat_window("__검색방__", P)
          and M.get_text(fake.search) == "" and fake.events("opened"), r)
    t_open = r["timings_ms"]
    r = M.open_chat("가족", execute=True, pids=P)
    time.sleep(0.2)
    check("open_chat 다른 방이 걸리면 실패·그 창 닫음", not r["ok"] and "가족여행" in r.get("error", "")
          and M.find_chat_window("가족여행", P) is None
          and any(e["room"] == "가족여행" for e in fake.events("closed")), r)

    # --- 시간 재기 (가짜 창, 30회)
    N = 30
    rows = []
    for i in range(N):
        tm = {}
        t0 = time.perf_counter()
        hh = M.find_chat_window(ROOM, P)
        tm["find_window"] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        ii = M.input_controls(hh)[0]
        tm["find_input"] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        M.get_text(ii)
        tm["read_draft"] = (time.perf_counter() - t0) * 1000
        M.deliver(ii, f"시간 재기 {i}\n둘째 줄", timings=tm)
        rows.append(tm)
    check(f"시간 재기 {N}회 모두 전송", len(fake.events("sent")) == 3 + N)
    keys = ["find_window", "find_input", "read_draft", "set_text", "verify_text", "send_click", "confirm_cleared"]
    print("\n--- 단계별 시간(ms, 가짜 창, %d회): 중앙값 / 최대" % N)
    tot = []
    for k in keys:
        vals = [r_[k] for r_ in rows]
        print(f"  {k:16s} {statistics.median(vals):7.2f} / {max(vals):7.2f}")
    for r_ in rows:
        tot.append(sum(r_[k] for k in keys))
    print(f"  {'합계':16s} {statistics.median(tot):7.2f} / {max(tot):7.2f}")
    print(f"  open_chat 실행(검색→창 뜸): {t_open}")
finally:
    fake.close()

# ---- 실제 PC 카카오톡: 켜져 있을 때만 읽기 전용
live = [p for p in subprocess.run(["tasklist", "/FI", "IMAGENAME eq KakaoTalk.exe", "/FO", "CSV", "/NH"],
                                   capture_output=True, text=True).stdout.splitlines() if "KakaoTalk" in p]
if live:
    pr = M.probe()
    print(f"\n[실제 카톡, 읽기 전용] 메인 창 {pr['main']}, 검색칸 {pr['search_edit']}, 열린 채팅창 {len(pr['chats'])}개")
    for c in pr["chats"]:
        print(f"  - {c['title']} class={c['class']} inputs={c['inputs']} minimized={c['minimized']}")
    check("실제 카톡 메인 창 찾음(읽기 전용)", bool(pr["main"]))
else:
    print("\nSKIP 실제 카톡 읽기 전용 확인: KakaoTalk.exe 실행 안 됨")

print(f"\n{'OK' if ok else 'FAILED'} {cnt}개")
sys.exit(0 if ok else 1)
