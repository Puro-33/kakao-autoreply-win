"""PC 카카오톡에서 남의 글에 하트 반응을 단다. 창 메시지(PostMessage)만 쓴다.

말풍선 우클릭, 메뉴 높이 확인, Reactions, 고르기 창 격자의 하트 클릭 순서다. 이미 하트가 있으면 누르지 않고,
메뉴·고르기 창·확인창이 남으면 WM_CLOSE 로 닫는다. 실패하면 아무것도 보내지 않고 사유만 돌려준다.
"""
from __future__ import annotations

import ctypes
import time

import pc_kakao_send as PK
import pc_kakao_thread as PT

user32 = PK.user32
gdi32 = ctypes.windll.gdi32

MENU_H = {"DirectChat": 340, "MultiChat": 282, "OM": 282}
MENU_COPY_Y = {340: 201, 282: 176}
MENU_Y_REACTIONS = 39
GRID_CLASS = "EVA_Window_Dblclk"
CHIP_MIN_PIXELS = 25


def _red_mask(px) -> bool:
    r, g, b = px[0], px[1], px[2]
    return r >= 200 and g <= 110 and b <= 130 and r - g >= 100


def red_count(img, box) -> int:
    x0, y0, x1, y1 = box
    w, h = img.size
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    n = 0
    for yy in range(y0, y1):
        for xx in range(x0, x1):
            if _red_mask(img.getpixel((xx, yy))):
                n += 1
    return n


def has_heart_chip(img, row_bottom: int, scale: float) -> bool:
    """말풍선 줄 맨 아래(row_bottom, 창 그림 기준) 근처 왼쪽에 빨간 하트가 있으면 이미 하트가 달린 것."""
    return red_count(img, (round(78 * scale / 1.25), row_bottom - round(30 * scale), round(140 * scale / 1.25),
                           row_bottom + round(8 * scale))) >= CHIP_MIN_PIXELS


def heart_point(img) -> tuple[int, int] | None:
    """고르기 창 격자 그림에서 첫 열의 빨간 하트 중 가장 아래 덩어리의 중심(Recently used 줄 것은 제외). 없으면 None."""
    w, h = img.size
    x0, x1 = 0, min(w, round(w * 0.25))
    rows = []
    for yy in range(h):
        n = sum(1 for xx in range(x0, x1) if _red_mask(img.getpixel((xx, yy))))
        rows.append(n)
    blobs, start = [], None
    for yy, n in enumerate(rows + [0]):
        if n >= 3 and start is None:
            start = yy
        elif n < 3 and start is not None:
            blobs.append((start, yy - 1))
            start = None
    blobs = [(a, b) for a, b in blobs if b - a >= 12 and sum(rows[a:b + 1]) >= 150]
    if not blobs:
        return None
    a, b = blobs[-1]
    xs = [xx for yy in range(a, b + 1) for xx in range(x0, x1) if _red_mask(img.getpixel((xx, yy)))]
    return (sum(xs) // len(xs), (a + b) // 2)


def _printwindow(hwnd: int):
    import ctypes.wintypes as W
    from PIL import Image
    x, y, w, h = PT._rect(hwnd)
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                    ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                    ("a", W.LONG), ("b", W.LONG), ("c", W.DWORD), ("d", W.DWORD)]
    bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def _grab(hwnd: int):
    from PIL import ImageGrab
    x, y, w, h = PT._rect(hwnd)
    return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).convert("RGB")


def react(room_name: str, target_text: str, *, room_type: str = "DirectChat", guard=None, pids=None,
          auto_open: bool = True) -> dict:
    r: dict = {"ok": False}
    want = PT._norm(target_text)
    if len(want) < 2:
        r["error"] = "대상 글이 너무 짧아 구분할 수 없음"
        return r
    mh = MENU_H.get(room_type)
    if not mh:
        r["error"] = f"{room_type} 방의 메뉴 높이를 몰라 달지 않음"
        return r
    old_h, old_y = PT.MENU_H_TEXT, PT.MENU_Y_COPY
    with PK._LOCK:
        tops0 = PT._tops(pids)
        hwnd = PK.find_chat_window(room_name, pids)
        if not hwnd:
            if not auto_open:
                r["error"] = "채팅창이 열려 있지 않음"
                return r
            o = PK.open_chat(room_name, execute=True, pids=pids)
            if not o.get("ok"):
                r["error"] = "방 열기 실패: " + str(o.get("error"))
                return r
            hwnd = o["hwnd"]
        iconic = bool(user32.IsIconic(hwnd))
        if iconic:
            user32.ShowWindow(hwnd, 4)
            time.sleep(0.4)
        saved, _ = PT.clip_get()
        try:
            PT.MENU_H_TEXT, PT.MENU_Y_COPY = mh, MENU_COPY_Y[mh]
            lst = PT._list(hwnd)
            if not lst:
                r["error"] = "메시지 목록 컨트롤 없음"
                return r
            s = PT._scale(hwnd)
            PT._close_menus(pids)
            user32.PostMessageW(lst, 0x0115, 7, 0)
            time.sleep(0.4)
            g0 = guard() if guard else None
            x = round(PT.BUBBLE_X * s)
            y = PT._rect(lst)[3] - round(15 * s)
            found = None
            for _ in range(24):
                if y <= PT.SCAN_TOP_MARGIN * s:
                    break
                got_t = PT.copy_at(lst, x, y, pids)
                if got_t is not None and PT._norm(got_t) == want:
                    found = y
                    break
                y -= round(PT.SCAN_STEP * s)
            if found is None:
                r["error"] = "대상 글을 화면에서 못 찾음"
                return r
            top_off = PT._rect(lst)[1] - PT._rect(hwnd)[1]
            try:
                chip = has_heart_chip(_printwindow(hwnd), top_off + found, s)
            except Exception as e:  # noqa: BLE001
                r["error"] = f"하트 칩 확인 실패({type(e).__name__}) - 달지 않음"
                return r
            if chip:
                r.update(ok=True, skipped="이미 하트가 있음")
                return r
            m = PT._menu_at(lst, x, found, pids)
            if not m:
                r["error"] = "대상 글의 메뉴 높이가 다름 - 달지 않음"
                return r
            before = PT._tops(pids)
            PT._mouse(m, round(PT.MENU_X * s), round(MENU_Y_REACTIONS * s))
            pk = PK._wait(lambda: next((t for t in PT._tops(pids) - before if PK.class_name(t) == "EVA_Window"), None), 1.5, 0.02)
            PT._close_menus(pids)
            if not pk:
                r["error"] = "반응 고르기 창이 안 뜸"
                return r
            time.sleep(0.4)
            grid = next((d for d in PK.descendants(pk) if PK.class_name(d) == GRID_CLASS and PT._rect(d)[2] > 50), None)
            if not grid:
                user32.PostMessageW(pk, 0x0010, 0, 0)
                r["error"] = "고르기 창 격자를 못 찾음"
                return r
            pt = heart_point(_grab(grid))
            if not pt:
                user32.PostMessageW(pk, 0x0010, 0, 0)
                r["error"] = "고르기 창에서 하트를 못 찾음"
                return r
            if guard and guard() != g0:
                user32.PostMessageW(pk, 0x0010, 0, 0)
                r["error"] = "그 사이 새 글이 와서 취소"
                return r
            PT._mouse(grid, pt[0], pt[1])
            time.sleep(1.0)
            r["ok"] = True
            return r
        except (PT.MenuStuck, PK.KakaoWindowError) as e:
            r["error"] = f"창 조작 중단: {e}"
            return r
        finally:
            PT.MENU_H_TEXT, PT.MENU_Y_COPY = old_h, old_y
            try:
                PT._close_menus(pids)
            except PT.MenuStuck:
                r["menu_stuck"] = True
            for t in PT._tops(pids) - tops0:
                user32.PostMessageW(t, 0x0010, 0, 0)
            time.sleep(0.3)
            r["new_windows_left"] = len(PT._tops(pids) - tops0)
            if saved is not None:
                PT.clip_set(saved)
            if iconic:
                user32.ShowWindow(hwnd, 6)
