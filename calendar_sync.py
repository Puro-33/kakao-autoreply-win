"""OWNER 구글 캘린더 연동 — 갠톡 일정 질문에 답하고, 빈 시간이면 약속을 직접 잡는다(사용자 결정 11/08).

- 읽기: claude.ai Google Calendar 커넥터를 `claude -p`로 부른다(허용 도구는 list_events 하나). 도구 결과 원본(JSON)을
  그대로 읽으므로 모델이 일정을 옮겨 적다 틀릴 일이 없다. 큰 결과는 CLI가 파일로 저장하므로 그 파일을 읽고 지운다.
- 캐시: calendar/cache.json(개인 데이터, 공개 금지). 봇은 이 캐시로만 바쁨/빔을 판단한다(메시지마다 커넥터를 부르지 않음).
- 공개 범위: 상대에게는 '바쁨/빔'만. 일정 제목은 절대 프롬프트에 넣지 않는다(수업만 '수업'으로 표시).
- 쓰기: create_event 하나만 허용한 별도 호출. 제목·시각은 코드가 만든 값만 넣는다(상대 메시지 문장을 넣지 않음).

실행: py -3.12 -I calendar_sync.py sync | show | free
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent
CACHE = BASE / "calendar" / "cache.json"
KST = dt.timezone(dt.timedelta(hours=9))
LIST_TOOL = "mcp__claude_ai_Google_Calendar__list_events"
CREATE_TOOL = "mcp__claude_ai_Google_Calendar__create_event"
_LOCK = threading.Lock()


def _cfg() -> dict:
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _run(prompt: str, tool: str, timeout: int = 240) -> list[dict]:
    """claude -p를 도구 하나만 허용해 돌리고, 그 도구의 결과(문자열)들을 돌려준다."""
    cfg = _cfg()
    exe = shutil.which(cfg.get("claude_cmd", "claude")) or cfg.get("claude_cmd", "claude")
    cmd = [exe, "-p", prompt, "--allowedTools", tool, "--output-format", "stream-json", "--verbose",
           "--model", cfg.get("calendar_model", "haiku")]
    cwd = BASE / "claude-cwd"
    cwd.mkdir(exist_ok=True)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
                       timeout=timeout, stdin=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    outs = []
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "user":
            continue
        for c in (ev.get("message") or {}).get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_result":
                continue
            cont = c.get("content")
            txt = cont if isinstance(cont, str) else "".join(
                x.get("text", "") for x in (cont or []) if isinstance(x, dict) and x.get("type") == "text")
            if txt:
                outs.append({"text": txt, "error": bool(c.get("is_error"))})
    return outs


def _payload(txt: str) -> dict | None:
    """도구 결과 문자열 → JSON. 너무 크면 CLI가 파일로 저장하고 경로만 알려 준다 → 그 파일을 읽고 지운다."""
    t = txt.strip()
    if t.startswith("{"):
        try:
            return json.loads(t)
        except ValueError:
            return None
    m = re.search(r"saved to (.+?\.txt)", t)
    if m:
        f = Path(m.group(1).strip())
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            f.unlink()
        except OSError:
            pass
        return d
    return None


def _when(x: dict) -> tuple[dt.datetime | None, bool]:
    if not isinstance(x, dict):
        return None, False
    if x.get("dateTime"):
        try:
            return dt.datetime.fromisoformat(x["dateTime"]).astimezone(KST), False
        except ValueError:
            return None, False
    if x.get("date"):
        try:
            d = dt.date.fromisoformat(x["date"])
            return dt.datetime(d.year, d.month, d.day, tzinfo=KST), True
        except ValueError:
            return None, True
    return None, False


def label(title: str) -> str:
    """상대에게 보여도 되는 이름. 제목은 비공개 → 수업만 '수업', 나머지는 '일정'."""
    return "수업" if re.match(r"\s*\[(수업|강의)", title or "") else "일정"


BOT_MARK = "카톡 자동응답 봇이 잡은 일정"   # create()가 설명 끝에 붙이는 표시
BOT_TITLE_RE = re.compile(r"약속\s*$")      # create()가 만드는 제목: '<이름> 약속' / '카톡 약속'


def is_bot_event(e: dict) -> bool:
    """봇이 잡은 일정인지(캐시의 bot 표시, 설명의 BOT_MARK, '… 약속' 제목). 제목·설명이 코드가 만든 값이라
    봇 쪽 일정 제목 노출 검사에서 뺀다. 설명 원문은 캐시에 저장하지 않고 이 표시만 남긴다."""
    if not isinstance(e, dict):
        return False
    return bool(e.get("bot")) or BOT_MARK in str(e.get("description") or "") \
        or bool(BOT_TITLE_RE.search(str(e.get("title") or e.get("summary") or "")))


def normalize(events: list[dict]) -> list[dict]:
    out = []
    for e in events or []:
        if e.get("status") == "cancelled":
            continue
        s, all_day = _when(e.get("start"))
        en, _ = _when(e.get("end"))
        if not s or not en:
            continue
        busy = e.get("transparency") != "transparent" and e.get("availability") != "AVAILABILITY_FREE" and not all_day
        out.append({"s": s.isoformat(), "e": en.isoformat(), "busy": busy, "all_day": all_day,
                    "label": label(e.get("summary", "")), "title": (e.get("summary") or "")[:80],
                    "bot": is_bot_event(e)})
    out.sort(key=lambda x: x["s"])
    return out


def sync(days: int = 21) -> dict:
    """캘린더를 읽어 캐시에 저장. 실패하면 예전 캐시를 그대로 둔다."""
    now = dt.datetime.now(KST)
    end = now + dt.timedelta(days=days)
    prompt = (f"Call {LIST_TOOL.split('__')[-1]} exactly once for the primary calendar with pageSize 250, "
              f"startTime {now:%Y-%m-%dT%H:%M:%S}, endTime {end:%Y-%m-%dT%H:%M:%S}, orderBy startTime. "
              "Do not call anything else. Then reply with just: done")
    events, ok = [], False
    for r in _run(prompt, LIST_TOOL):
        d = _payload(r["text"])
        if d and isinstance(d.get("events"), list):
            events, ok = d["events"], True
            break
    if not ok:
        return {"ok": False, "why": "캘린더 읽기 실패"}
    data = {"at": now.timestamp(), "synced": now.isoformat(timespec="seconds"), "days": days, "events": normalize(events)}
    with _LOCK:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CACHE)
    return {"ok": True, "n": len(data["events"])}


def load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fresh(max_age_min: float = 90) -> bool:
    d = load()
    return bool(d) and dt.datetime.now(KST).timestamp() - float(d.get("at", 0)) < max_age_min * 60


def busy_blocks(days: int = 7, data: dict | None = None) -> list[tuple[dt.datetime, dt.datetime, str]]:
    d = data or load()
    now = dt.datetime.now(KST)
    lim = now + dt.timedelta(days=days)
    out = []
    for e in d.get("events", []):
        if not e.get("busy"):
            continue
        s, en = dt.datetime.fromisoformat(e["s"]), dt.datetime.fromisoformat(e["e"])
        if en > now and s < lim:
            out.append((s, en, e.get("label", "일정")))
    return out


def _hours() -> tuple[int, int]:
    h = _cfg().get("schedule_hours", [9, 22])
    return int(h[0]), int(h[1])


def free_slots(days: int = 7, min_minutes: int = 60, data: dict | None = None) -> list[tuple[dt.datetime, dt.datetime]]:
    """깨어 있는 시간(schedule_hours) 안에서 비어 있는 구간."""
    h0, h1 = _hours()
    now = dt.datetime.now(KST)
    # 하루 더 읽는다: 아래 반복은 오늘~days일 뒤까지(days+1일)라, busy_blocks(days)로는 마지막 날 지금 시각 이후 일정이
    # 빠져 그날이 통째로 비어 보였다(11/09)
    blocks = sorted(busy_blocks(days + 1, data))
    out = []
    for k in range(days + 1):
        day = (now + dt.timedelta(days=k)).date()
        a = dt.datetime(day.year, day.month, day.day, h0, tzinfo=KST)
        b = dt.datetime(day.year, day.month, day.day, h1, tzinfo=KST)
        if k == 0:
            a = max(a, (now + dt.timedelta(minutes=30)).replace(second=0, microsecond=0))
            a = a.replace(minute=0 if a.minute == 0 else (30 if a.minute <= 30 else 0)) + (
                dt.timedelta(hours=1) if a.minute > 30 else dt.timedelta())
        cur = a
        for s, e, _ in blocks:
            if e <= cur or s >= b:
                continue
            if s > cur and (s - cur).total_seconds() >= min_minutes * 60:
                out.append((cur, min(s, b)))
            cur = max(cur, e)
        if cur < b and (b - cur).total_seconds() >= min_minutes * 60:
            out.append((cur, b))
    return out


DAYS = "월화수목금토일"


def _d(t: dt.datetime) -> str:
    return f"{t:%m/%d}({DAYS[t.weekday()]})"


def prompt_block(days: int = 7) -> str:
    """갠톡 프롬프트용: 바쁜 시간(내용 비공개)과 빈 시간. 캐시가 없거나 오래되면 빈 문자열."""
    if not fresh(CFG_MAX_AGE()):
        return ""
    by: dict[str, list[str]] = {}
    for s, e, lab in busy_blocks(days):
        by.setdefault(_d(s), []).append(f"{s:%H:%M}-{e:%H:%M} {lab}")
    fr: dict[str, list[str]] = {}
    for s, e in free_slots(days):
        fr.setdefault(_d(s), []).append(f"{s:%H:%M}-{e:%H:%M}")
    now = dt.datetime.now(KST)
    lines = [f"[OWNER 일정 — 지금 {_d(now)} {now:%H:%M}. 바쁜 시간만 적었고 내용은 비공개(제목·과목·장소를 지어내거나 말하지 않는다)]"]
    lines += [f"- {k}: {', '.join(v)}" for k, v in by.items()] or ["- (앞으로 7일 바쁜 일정 없음)"]
    lines.append(f"[비어 있는 시간 — {_hours()[0]}시~{_hours()[1]}시 중 1시간 이상]")
    lines += [f"- {k}: {', '.join(v)}" for k, v in fr.items()] or ["- (없음)"]
    return "\n".join(lines)


def CFG_MAX_AGE() -> float:
    return float(_cfg().get("calendar_max_age_min", 90))


def is_free(start: dt.datetime, end: dt.datetime, data: dict | None = None) -> bool:
    for s, e, _ in busy_blocks(31, data):
        if s < end and e > start:
            return False
    return True


def booking_problem(start: dt.datetime, end: dt.datetime) -> str:
    """잡아도 되는 약속인지 코드가 본다. 문제가 있으면 이유."""
    now = dt.datetime.now(KST)
    h0, h1 = _hours()
    if start.tzinfo is None:
        start, end = start.replace(tzinfo=KST), end.replace(tzinfo=KST)
    if start < now + dt.timedelta(minutes=30):
        return "너무 가까운 시각"
    if start > now + dt.timedelta(days=int(_cfg().get("schedule_max_days", 30))):
        return "너무 먼 날짜"
    dur = (end - start).total_seconds() / 60
    if dur < 15 or dur > 240:
        return "길이 이상"
    if start.hour < h0 or (end.hour, end.minute) > (h1, 0) or end.date() != start.date():
        return "깨어 있는 시간 밖"
    if not fresh(CFG_MAX_AGE()):
        return "캘린더 캐시 오래됨"
    if not is_free(start, end):
        return "그 시간에 다른 일정"
    return ""


def parse_iso(s: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(s).strip())
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=KST)


SAFE_TITLE = re.compile(r"[^0-9A-Za-z가-힣·\s_()\-]")


def create(title: str, start: dt.datetime, end: dt.datetime, note: str = "") -> str:
    """일정 생성(코드가 만든 값만). 성공하면 이벤트 id, 실패하면 ''. 만들기 직전에 다시 읽어 겹치는지 한 번 더 본다."""
    sync(days=int(_cfg().get("schedule_max_days", 30)) + 1)
    if booking_problem(start, end):
        return ""
    title = SAFE_TITLE.sub("", title)[:60] or "카톡 약속"
    note = SAFE_TITLE.sub("", note)[:200]
    prompt = (f"Call {CREATE_TOOL.split('__')[-1]} exactly once on the primary calendar with: "
              f"summary \"{title}\", startTime {start:%Y-%m-%dT%H:%M:%S}, endTime {end:%Y-%m-%dT%H:%M:%S}, "
              f"description \"{note} ({BOT_MARK})\", notificationLevel NONE, no attendees. "
              "Do not call anything else. Then reply with just: done")
    for r in _run(prompt, CREATE_TOOL):
        if r["error"]:
            continue
        d = _payload(r["text"])
        if d and d.get("id"):
            sync(days=21)
            return str(d["id"])
    return ""


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "sync":
        print(sync())
    elif cmd == "free":
        for s, e in free_slots():
            print(_d(s), f"{s:%H:%M}-{e:%H:%M}")
    else:
        print(prompt_block() or "(캐시 없음 — sync 먼저)")
