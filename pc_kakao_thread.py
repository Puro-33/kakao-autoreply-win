"""PC 카카오톡 스레드(오픈채팅 댓글) 답글 모드를 창 메시지(PostMessage)만으로 켜고 끈다.

대상 말풍선 우클릭 → 메뉴의 Copy 로 글을 대조해 위치를 찾고, 'Reply in thread' 를 누른다.
클립보드를 잠깐 쓴다(글은 되돌리고, 글이 아닌 내용은 되돌리지 못한다).
좌표는 26.9.2 영어 UI에서 잰 값이다.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as W
import re
import time

import pc_kakao_send as PK

user32, kernel32 = PK.user32, PK.kernel32
MENU_CLASS = "EVA_Menu"
LIST_PREFIX = "EVA_VH_ListControl"
MENU_H_TEXT = 282
MENU_H_TOL = 6
MENU_X = 50
MENU_Y_REPLY = 15
MENU_Y_COPY = 176
BUBBLE_X = 100
SCAN_STEP = 22
SCAN_TOP_MARGIN = 30
INPUT_X_NORMAL = 13.5
CANCEL_FROM_RIGHT = 26.5
CANCEL_ABOVE_INPUT = 36

for _f, _a, _r in ((user32.GetWindowRect, [W.HWND, ctypes.POINTER(W.RECT)], W.BOOL), (user32.PostMessageW, [W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
                   (user32.OpenClipboard, [W.HWND], W.BOOL), (user32.GetClipboardData, [W.UINT], W.HANDLE),
                   (user32.SetClipboardData, [W.UINT, W.HANDLE], W.HANDLE), (user32.CountClipboardFormats, [], ctypes.c_int),
                   (kernel32.GlobalLock, [W.HANDLE], ctypes.c_void_p), (kernel32.GlobalUnlock, [W.HANDLE], W.BOOL),
                   (kernel32.GlobalAlloc, [W.UINT, ctypes.c_size_t], W.HANDLE)):
    _f.argtypes, _f.restype = _a, _r


def _rect(h: int) -> tuple[int, int, int, int]:
    old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        r = W.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    finally:
        if old:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))


def _scale(h: int) -> float:
    return (user32.GetDpiForWindow(h) or 96) / 96.0


def _open_clip() -> bool:
    for _ in range(15):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clip_get() -> tuple[str | None, bool]:
    """(글, 글이 아닌 내용이 있었는가). 못 열면 (None, False)."""
    if not _open_clip():
        return None, False
    try:
        h = user32.GetClipboardData(13)
        if not h:
            return "", user32.CountClipboardFormats() > 0
        p = kernel32.GlobalLock(h)
        s = ctypes.wstring_at(p) if p else ""
        kernel32.GlobalUnlock(h)
        return s, False
    finally:
        user32.CloseClipboard()


def clip_set(s: str) -> bool:
    if not _open_clip():
        return False
    try:
        user32.EmptyClipboard()
        if s:
            b = (s + "\0").encode("utf-16-le")
            h = kernel32.GlobalAlloc(0x0002, len(b))
            p = kernel32.GlobalLock(h)
            ctypes.memmove(p, b, len(b))
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(13, h)
        return True
    finally:
        user32.CloseClipboard()


def _mouse(h: int, x: int, y: int, right: bool = False) -> None:
    lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        user32.PostMessageW(h, 0x0200, 0, lp)
        down, up, wp = (0x0204, 0x0205, 2) if right else (0x0201, 0x0202, 1)
        user32.PostMessageW(h, down, wp, lp)
        time.sleep(0.04)
        user32.PostMessageW(h, up, 0, lp)
    finally:
        if old:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))


def _menus(pids=None) -> list[int]:
    return [t for t in PK.top_windows(pids) if PK.class_name(t) == MENU_CLASS and user32.IsWindowVisible(t)]


class MenuStuck(RuntimeError):
    pass


def _tops(pids=None) -> set:
    return {t for t in PK.top_windows(pids) if user32.IsWindowVisible(t)}


def _close_menus(pids=None) -> None:
    """메뉴가 떠 있는 동안 넣은 클릭은 메뉴 항목으로 들어간다. 클릭 대신 WM_CLOSE 로만 닫는다."""
    for _ in range(4):
        ms = _menus(pids)
        if not ms:
            return
        for m in ms:
            user32.PostMessageW(m, 0x0010, 0, 0)
        time.sleep(0.12)
    if _menus(pids):
        raise MenuStuck("메뉴가 닫히지 않음")


def _guard_new_windows(before: set, pids=None) -> list:
    """작업 중 새로 뜬 창(확인창 등)을 WM_CLOSE 로 닫는다. Enter·OK는 보내지 않는다."""
    extra = [t for t in _tops(pids) - before if PK.class_name(t) != MENU_CLASS]
    for t in extra:
        user32.PostMessageW(t, 0x0010, 0, 0)
    if extra:
        time.sleep(0.2)
    return [PK.class_name(t) for t in extra]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _list(chat_hwnd: int) -> int | None:
    return next((h for h in PK.descendants(chat_hwnd) if PK.class_name(h).startswith(LIST_PREFIX) and user32.IsWindowVisible(h)), None)


def in_reply_mode(chat_hwnd: int) -> bool:
    inputs = PK.input_controls(chat_hwnd)
    if len(inputs) != 1:
        return False
    s = _scale(chat_hwnd)
    return (_rect(inputs[0])[0] - _rect(chat_hwnd)[0]) > (INPUT_X_NORMAL + 8) * s


def _menu_at(lst: int, x: int, y: int, pids=None) -> int | None:
    """목록의 (x,y)를 우클릭해 '남의 글' 메뉴가 뜨면 그 창, 아니면 닫고 None."""
    _close_menus(pids)
    _mouse(lst, x, y, right=True)
    m = PK._wait(lambda: (_menus(pids) or [None])[0], 1.0, 0.01)
    if not m:
        time.sleep(0.25)
        _close_menus(pids)
        return None
    s = _scale(lst)
    if len(_menus(pids)) != 1 or abs(_rect(m)[3] - MENU_H_TEXT * s) > MENU_H_TOL * s:
        _close_menus(pids)
        return None
    return m


def copy_at(lst: int, x: int, y: int, pids=None) -> str | None:
    """그 점 말풍선의 글(없거나 남의 글이 아니면 None). 클립보드를 쓴다 — 호출자가 저장·복구한다."""
    clip_set("")
    m = _menu_at(lst, x, y, pids)
    if not m:
        return None
    s = _scale(lst)
    _mouse(m, round(MENU_X * s), round(MENU_Y_COPY * s))
    got = PK._wait(lambda: clip_get()[0] or None, 0.4, 0.01)
    _close_menus(pids)
    return got


def _settle(h: int, timeout: float = 1.0) -> None:
    end = time.time() + timeout
    last = _rect(h)
    while time.time() < end:
        time.sleep(0.15)
        cur = _rect(h)
        if cur == last:
            return
        last = cur


def cancel(chat_hwnd: int) -> bool:
    """답글 모드를 끈다(배너의 X). 꺼졌으면 True."""
    _settle(PK.input_controls(chat_hwnd)[0], 0.6)
    if not in_reply_mode(chat_hwnd):
        return True
    s = _scale(chat_hwnd)
    wx, wy, ww, _ = _rect(chat_hwnd)
    ix, iy, _, _ = _rect(PK.input_controls(chat_hwnd)[0])
    _mouse(chat_hwnd, ww - round(CANCEL_FROM_RIGHT * s), (iy - wy) - round(CANCEL_ABOVE_INPUT * s))
    PK._wait(lambda: not in_reply_mode(chat_hwnd), 1.0, 0.02)
    _settle(PK.input_controls(chat_hwnd)[0])
    return not in_reply_mode(chat_hwnd)


def enter(chat_hwnd: int, target_text: str, guard=None, pids=None, max_probes: int = 24,
          max_pages: int = 4) -> dict:
    """target_text 말풍선의 답글 모드를 켠다(전송은 하지 않는다).

    guard 는 그 방의 마지막 메시지 id를 돌려주는 함수다. 시작과 끝의 값이 다르면 새 글이 온 것으로 보고 취소한다.
    """
    r: dict = {"ok": False, "probes": 0}
    want = _norm(target_text)
    if len(want) < 2:
        r["error"] = "대상 글이 너무 짧아 구분할 수 없음"
        return r
    was_iconic = bool(user32.IsIconic(chat_hwnd))
    if was_iconic:
        user32.ShowWindow(chat_hwnd, 4)
        time.sleep(0.3)
    r["restored"] = was_iconic
    if in_reply_mode(chat_hwnd) and not cancel(chat_hwnd):
        r["error"] = "이미 답글 모드인데 끄지 못함"
        return r
    lst = _list(chat_hwnd)
    if not lst:
        r["error"] = "메시지 목록 컨트롤 없음"
        return r
    s = _scale(chat_hwnd)
    saved, nontext = clip_get()
    r["clipboard_nontext_lost"] = nontext
    g0 = guard() if guard else None
    x = round(BUBBLE_X * s)
    tops0 = _tops(pids)
    try:
        _close_menus(pids)
        user32.PostMessageW(lst, 0x0115, 7, 0)
        time.sleep(0.3)
        found = None
        for page in range(max_pages + 1):
            if page:
                user32.PostMessageW(lst, 0x0115, 2, 0)
                time.sleep(0.6)
            lh = _rect(lst)[3]
            y = lh - round(15 * s)
            for _ in range(max_probes):
                if y <= SCAN_TOP_MARGIN * s:
                    break
                r["probes"] += 1
                got = copy_at(lst, x, y, pids)
                if got is not None and _norm(got) == want:
                    found = y
                    break
                y -= round(SCAN_STEP * s)
            if found is not None:
                break
        r["pages"] = page
        if found is None:
            user32.PostMessageW(lst, 0x0115, 7, 0)
            r["error"] = "대상 글을 목록에서 못 찾음"
            return r
        m = _menu_at(lst, x, found, pids)
        if not m:
            r["error"] = "대상 글의 메뉴가 다시 안 뜸"
            return r
        _mouse(m, round(MENU_X * s), round(MENU_Y_REPLY * s))
        if not PK._wait(lambda: in_reply_mode(chat_hwnd), 1.2, 0.02):
            _close_menus(pids)
            r["error"] = "답글 모드로 안 바뀜(이 방은 스레드를 안 쓰거나 메뉴 배치가 다름)"
            return r
        _settle(lst)
        shrink = lh - _rect(lst)[3]
        again = None
        y2 = _rect(lst)[3] - round(15 * s)
        for _ in range(max_probes):
            if y2 <= SCAN_TOP_MARGIN * s:
                break
            again = copy_at(lst, x, y2, pids)
            if again is not None and _norm(again) == want:
                break
            y2 -= round(SCAN_STEP * s)
        g1 = guard() if guard else None
        if again is None or _norm(again) != want or g0 != g1:
            cancel(chat_hwnd)
            r["moved"] = g0 != g1
            r["error"] = "답글 모드를 켠 뒤 새 글이 와서 취소" if r["moved"] else "답글 모드를 켠 뒤 대상 글 재확인 실패"
            return r
        odd = _guard_new_windows(tops0, pids)
        if odd:
            cancel(chat_hwnd)
            r["error"] = f"뜻밖의 창이 떠서 닫고 취소함: {odd}"
            return r
        r.update(ok=True, y=found, shrink=shrink)
        return r
    except (MenuStuck, PK.KakaoWindowError) as e:
        r["error"] = f"창 조작 중단: {e}"
        try:
            cancel(chat_hwnd)
        except Exception:
            pass
        return r
    finally:
        try:
            _close_menus(pids)
        except MenuStuck:
            r["menu_stuck"] = True
        odd = _guard_new_windows(tops0, pids)
        if odd and r.get("ok"):
            r["ok"] = False
            r["error"] = f"뜻밖의 창이 떠서 닫음: {odd}"
            try:
                cancel(chat_hwnd)
            except Exception:
                pass
        if saved is not None:
            clip_set(saved)
        if not r["ok"] and was_iconic:
            user32.ShowWindow(chat_hwnd, 6)
