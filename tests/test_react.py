"""pc_kakao_react.py 시험. 실제 카톡 창·반응은 건드리지 않는다.

창 호출(PT/PK/user32)을 기록용 가짜로 바꿔 클릭 순서·좌표를 확인하고, 하트 칩·하트 위치 감지는 직접 그린 그림으로 시험한다.
실행: py -3.12 -I tests\\test_react.py   (마지막 줄 'N checks: ALL OK')
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
from PIL import Image, ImageDraw  # noqa: E402

import pc_kakao_react as R  # noqa: E402

n, ok = [0], [True]


def check(name, cond, extra=""):
    n[0] += 1
    if not cond:
        ok[0] = False
    print(("OK   " if cond else "FAIL ") + name + (f" {extra}" if extra and not cond else ""))


def heart_img(size, spots):
    im = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(im)
    for (cx, cy) in spots:
        d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=(235, 50, 60))
    return im


chip = heart_img((475, 562), [(97, 388)])
check("칩 있음 → True", R.has_heart_chip(chip, 398, 1.25) is True)
check("칩 없음 → False", R.has_heart_chip(Image.new("RGB", (475, 562), (190, 210, 230)), 398, 1.25) is False)
check("주황 하트(다른 반응)는 빨강으로 안 침", R.has_heart_chip(heart_img((475, 562), []), 398, 1.25) is False)
orange = Image.new("RGB", (475, 562), (255, 255, 255))
ImageDraw.Draw(orange).ellipse((86, 377, 108, 399), fill=(255, 140, 20))
check("주황은 칩으로 안 침", R.has_heart_chip(orange, 398, 1.25) is False)
check("고르기 창: 하트 하나 → 그 자리", R.heart_point(heart_img((373, 468), [(46, 74)])) == (46, 74))
two = R.heart_point(heart_img((373, 468), [(46, 30), (46, 74)]))
check("Recently used 줄에도 빨간 하트가 있으면 가장 아래(Basic) 것을 고른다", two == (46, 74), str(two))
check("하트가 없으면 None", R.heart_point(Image.new("RGB", (373, 468), (255, 255, 255))) is None)


class Rec:
    def __init__(self, **kw):
        self.ev = []
        self.kw = kw
        self.tops = {10}
        self.picker_open = False

    def install(self):
        rec = self
        PT, PK = R.PT, R.PK

        class U:
            def IsIconic(self, h): return 0
            def ShowWindow(self, h, c): rec.ev.append(("show", c))
            def PostMessageW(self, h, m, w, l):
                rec.ev.append(("post", h, hex(m), w))
                if m == 0x0010 and h == 77:
                    rec.picker_open = False
                    rec.tops.discard(77)
                return 1
        R.user32 = U()
        R.time.sleep = lambda s: None

        PK.find_chat_window = lambda name, pids=None: 5
        PK.class_name = lambda h: {77: "EVA_Window", 88: R.GRID_CLASS}.get(h, "X")
        PK.descendants = lambda h: [88]
        PK._wait = lambda pred, t, i=0.01: pred()
        PT._rect = lambda h: {5: (0, 0, 475, 562), 6: (0, 111, 473, 306), 88: (1, 93, 373, 468), 77: (0, 0, 375, 562)}.get(h, (0, 0, 100, 100))
        PT._scale = lambda h: 1.25
        PT._list = lambda h: 6
        PT._close_menus = lambda *a, **k: None
        PT._norm = lambda s: s
        PT.clip_get = lambda: ("old", False)
        PT.clip_set = lambda s: rec.ev.append(("clip_set", s))
        PT.copy_at = lambda lst, x, y, pids=None: (rec.kw.get("text", "다음주에 봬요!") if y == 287 else None)
        PT._menu_at = lambda lst, x, y, pids=None: (33 if rec.kw.get("menu", True) else None)
        PT._mouse = lambda h, x, y, right=False: (rec.ev.append(("mouse", h, x, y)), rec.tops.add(77) if h == 33 else None, None)[2]
        PT._tops = lambda pids=None: set(rec.tops)
        R._printwindow = lambda h: (_ for _ in ()).throw(RuntimeError("boom")) if rec.kw.get("pw_fail") else (chip if rec.kw.get("chip") else Image.new("RGB", (475, 562), (190, 210, 230)))
        R._grab = lambda h: heart_img((373, 468), [(46, 30), (46, 74)])
        return self


def run(**kw):
    rec = Rec(**kw).install()
    g = iter(kw.get("guards", [1, 1, 1]))
    r = R.react("방", "다음주에 봬요!", guard=lambda: next(g, 1))
    return r, rec.ev


r, ev = run()
clicks = [e for e in ev if e[0] == "mouse"]
check("정상: ok 이고 새 창 0", r.get("ok") is True and not r.get("skipped") and r.get("new_windows_left") in (0, 1), str(r))
check("정상: Reactions 클릭(메뉴 창, 논리 39×1.25) 다음 격자 창에서 Basic 하트 (46,74) 클릭",
      [(c[1], c[2], c[3]) for c in clicks if c[1] in (33, 88)] == [(33, 62, 49), (88, 46, 74)], str(clicks))
check("정상: Recently used 줄 자리(46,30)는 누르지 않음", all((c[2], c[3]) != (46, 30) for c in clicks if c[1] == 88))
check("정상: 클립보드를 원래 값으로 되돌림", ("clip_set", "old") in ev)
r, ev = run(chip=True)
check("이미 하트가 있으면 안 누르고 skipped", r.get("ok") and r.get("skipped") and not any(e[0] == "mouse" and e[1] in (33, 88) for e in ev), str(r))
r, ev = run(pw_fail=True)
check("칩 확인이 실패하면 안 누르고 에러", not r["ok"] and "칩 확인 실패" in r["error"] and not any(e[0] == "mouse" and e[1] in (33, 88) for e in ev), str(r))
r, ev = run(menu=False)
check("메뉴 높이가 다르면 안 누르고 에러", not r["ok"] and "메뉴" in r["error"] and not any(e[0] == "mouse" and e[1] in (33, 88) for e in ev), str(r))
r, ev = run(text="다른 글")
check("대상 글이 안 보이면 에러(아무것도 안 누름)", not r["ok"] and "못 찾음" in r["error"] and not any(e[0] == "mouse" and e[1] in (33, 88) for e in ev), str(r))
r, ev = run(guards=[1, 2])
check("그 사이 새 글이 오면 고르기 창을 WM_CLOSE 로 닫고 하트를 안 누름",
      not r["ok"] and "새 글" in r["error"] and not any(e[0] == "mouse" and e[1] == 88 for e in ev) and ("post", 77, "0x10", 0) in ev, str(r))
Rec().install()
r = R.react("방", "다음주에 봬요!", room_type="OD")
check("메뉴 높이를 모르는 방 종류는 달지 않음", not r["ok"] and "메뉴 높이" in r["error"], str(r))
r = R.react("방", "a")
check("대상 글이 너무 짧으면 달지 않음", not r["ok"])
print(f"{n[0]} checks:", "ALL OK" if ok[0] else "SOME FAILED")
