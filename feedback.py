"""원트랙 피드백 파이프라인 (사용자 지시 2026-11-08).

카톡 봇 답·고민방 대리 답·게임 채팅/내 말·OWNER의 지적을 한 원장(feedback/ledger.jsonl)에 모으고,
reflect()가 Claude로 "앞으로 지킬 규칙"을 뽑아 안전 검사를 통과한 것만 feedback/learned_rules.json에 반영한다.
봇·대리 답·게임 에이전트는 rules_block(채널)로 그 규칙을 프롬프트에 넣는다(파일 수정 시각으로 자동 갱신).

- 원장에는 개인정보 패턴을 가린 글만 넣는다.
- 규칙은 말투·태도에 관한 것만 허용. '먼저 보내라/약속 확정/개인정보 말해라/보류 풀어라' 같은 안전 장치 해제 규칙은 버린다.
- 규칙 수 상한(채널별 12개), 한 규칙 120자. OWNER 지적에서 나온 규칙이 자동 관찰보다 우선.
실행: py -3.12 -I feedback.py reflect | show | critique "<지적>" [--channel kakao|proxy|game|all]
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
DIR = BASE / "feedback"
LEDGER = DIR / "ledger.jsonl"
RULES = DIR / "learned_rules.json"
KST = dt.timezone(dt.timedelta(hours=9))
CHANNELS = ("all", "kakao", "proxy")
_LOCK = threading.Lock()
_MASK = [re.compile(p) for p in (r"01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}", r"(?<![\d-])(?=(?:\d[-\s]?){10,})(?:\d{2,6}[-\s]){2,5}\d{2,8}(?!\d)",
                                  r"(?<!\d)\d{10,16}(?!\d)", r"(?<!\d)\d{8}(?!\d)", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")]
UNSAFE = re.compile(r"먼저\s?(보내|연락|말\s?걸)|선제|예약\s?발송|약속\s?(을\s?)?(잡|확정|수락)|확정해|송금|계좌|돈을?\s?(보내|빌려)|"
                    r"학번|전화번호|주소를?\s?(알려|말)|개인정보를?\s?(알려|말)|보류(를|하지)?\s?(말|풀|없애)|hold\s?(하지|금지)|"
                    r"AI(라|임)?(을|를)?\s?(부정|아니라고)|사람이라고\s?(말|답)|입시|성적|병역")


BANNED_EXAMPLE = re.compile(r"하셨겠어요|힘들었겠|힘드셨겠|속상했겠|속상하셨겠|마음이\s?느껴|충분히\s?이해|그랬군요|군요|어떤\s?마음|"
                            r"알려\s?주셔서\s?감사|참고해\s?볼게|소아과|병원|상담\s?센터")
REGISTER_RE = re.compile(r"존댓말|반말|~요\s?위주|해요체|합쇼체|줄임말|은어")


def _bad_example(rule: str, channel: str) -> str:
    """규칙 안 따옴표 예시가 금지 표현이면 그 표현. '~말고'·'금지'처럼 하지 말라는 쪽에 든 예시는 허용."""
    for q in re.findall(r"['‘“\"]([^'’”\"]{1,40})['’”\"]", rule):
        m = BANNED_EXAMPLE.search(q)
        if not m:
            continue
        after = rule[rule.find(q) + len(q): rule.find(q) + len(q) + 24]
        before = rule[max(0, rule.find(q) - 6): rule.find(q)]
        if re.search(r"말고|대신|금지|쓰지|하지\s?말|빼|않", after + before):
            continue
        return q
    return ""


def _mask(s: str) -> str:
    for p in _MASK:
        s = p.sub("[가림]", s or "")
    return s


def add(source: str, **kw) -> None:
    """원장에 한 줄. source: kakao|proxy|owner."""
    DIR.mkdir(parents=True, exist_ok=True)
    rec = {"at": dt.datetime.now(KST).isoformat(timespec="seconds"), "source": source}
    for k, v in kw.items():
        rec[k] = _mask(v)[:800] if isinstance(v, str) else v
    with _LOCK, open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def critique(text: str, channel: str = "all") -> None:
    add("owner", kind="critique", text=text, channel=channel if channel in CHANNELS else "all")


def _load_rules() -> dict:
    try:
        return json.loads(RULES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated_at": "", "rules": {c: [] for c in CHANNELS}, "rejected": []}


_CACHE = {"mtime": 0.0, "rules": None}


def rules_block(channel: str) -> str:
    """프롬프트에 넣을 [최근 피드백으로 배운 규칙] 블록(전역 + 채널)."""
    try:
        m = RULES.stat().st_mtime
    except OSError:
        return ""
    if m != _CACHE["mtime"]:
        _CACHE["rules"], _CACHE["mtime"] = _load_rules(), m
    r = (_CACHE["rules"] or {}).get("rules", {})
    lines = [x["text"] for x in r.get("all", [])] + [x["text"] for x in r.get(channel, [])]
    if not lines:
        return ""
    return "[피드백으로 배운 규칙 — OWNER 지적과 실제 대화에서 자동 반영. 위 안전 규칙(보류·먼저 안 보냄·비공개)이 항상 우선]\n" + \
        "\n".join(f"- {x}" for x in lines)


def _read_ledger(limit: int = 1500) -> list[dict]:
    try:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return out


# ── B8 OWNER 판정 고리: '!판정'(나와의 채팅)에서 지난주 봇 답 몇 건을 보여 주고 '1 좋음'·'2 나쁨 이유'를 owner_judge로 남긴다 ──
JUDGE_LINE = re.compile(r"^\s*([1-9])\s*(?:번|[.):])?\s*(좋음|좋아|굿|ㅇㅋ|good|나쁨|나빠|별로|bad)(?![가-힣A-Za-z])\s*[,.:\-]?\s*(.*)$", re.I)
_GOOD = ("좋음", "좋아", "굿", "ㅇㅋ", "good")


def judge_ref(rec: dict) -> str:
    """원장 bot_reply 한 줄의 고정 id(판정이 어느 답에 대한 것인지)."""
    import hashlib
    return hashlib.sha1(f"{rec.get('at', '')}|{rec.get('text', '')}".encode("utf-8")).hexdigest()[:12]


def _at(rec: dict):
    try:
        return dt.datetime.fromisoformat(str(rec.get("at")))
    except (TypeError, ValueError):
        return None


def judge_pick(n: int = 5, days: float = 7, now: dt.datetime | None = None, seed=None,
               channels=("kakao", "proxy"), window_sec: float = 6 * 3600) -> list[dict]:
    """지난 days일 봇 답 중 아직 판정 안 한 것 n건. OWNER이 그 뒤 직접 쓴 말(owner_followup, 같은 방 키 rk)이 있는 답을 먼저.
    돌려주는 항목: ref, ch, room(가림용 원래 이름), ctx, bot, owner."""
    now = now or dt.datetime.now(KST)
    recs = _read_ledger(5000)
    judged = {r.get("ref") for r in recs if r.get("kind") == "owner_judge"}
    lo = now - dt.timedelta(days=days)
    follows = [r for r in recs if r.get("kind") == "owner_followup" and r.get("rk") is not None]
    replies = [r for r in recs if r.get("kind") == "bot_reply" and r.get("source") in channels]
    cand = []
    for i, r in enumerate(replies):
        t = _at(r)
        if t is None or not (lo <= t <= now) or judge_ref(r) in judged or not str(r.get("text") or "").strip():
            continue
        own = ""
        if r.get("rk") is not None:
            nxt = next((_at(x) for x in replies[i + 1:] if x.get("rk") == r.get("rk") and _at(x)), None)
            for f in follows:
                ft = _at(f)
                if f.get("rk") == r.get("rk") and ft and t < ft and (nxt is None or ft <= nxt) \
                        and (ft - t).total_seconds() <= window_sec:
                    own = str(f.get("text") or "")
                    break
        cand.append({"ref": judge_ref(r), "ch": r.get("source"), "room": str(r.get("room") or ""),
                     "ctx": str(r.get("ctx") or ""), "bot": str(r.get("text") or ""), "owner": own, "at": str(r.get("at"))})
    import random as _rnd
    rng = _rnd.Random(seed if seed is not None else now.strftime("%G-%V"))
    with_own = [c for c in cand if c["owner"]]
    rest = [c for c in cand if not c["owner"]]
    rng.shuffle(with_own)
    rng.shuffle(rest)
    out = (with_own + rest)[:max(0, n)]
    return sorted(out, key=lambda c: c["at"])


def judge_parse(text: str) -> list[tuple[int, str, str]]:
    """'1 좋음' / '2 나쁨 너무 길어' 줄들 → [(번호, 'good'|'bad', 이유)]. 한 줄이라도 형식이 안 맞으면 빈 목록(일반 메모로 둠)."""
    out = []
    lines = [l for l in (text or "").splitlines() if l.strip()]
    for l in lines:
        m = JUDGE_LINE.match(l)
        if not m:
            return []
        out.append((int(m.group(1)), "good" if m.group(2).lower() in _GOOD else "bad", m.group(3).strip()[:300]))
    return out


def judge_add(item: dict, verdict: str, reason: str = "") -> None:
    add("owner", kind="owner_judge", ref=item.get("ref", ""), channel=item.get("ch") or "all", verdict=verdict,
        reason=reason, bot=item.get("bot", "")[:300], owner=item.get("owner", "")[:300], ctx=item.get("ctx", "")[:200])


def _unique_judges(recs) -> list[dict]:
    """같은 답(ref)에 판정이 여러 번이면 마지막 것만."""
    last = {}
    for r in recs:
        if r.get("kind") == "owner_judge" and r.get("ref"):
            last.pop(r["ref"], None)
            last[r["ref"]] = r
    return list(last.values())


REFLECT_SYSTEM = """너는 카톡 자동응답 봇의 말투 코치다. 아래 [원장]은 봇이 보낸 답, 실제 사람들이 쓴 말(오픈채팅), 계정 주인(OWNER)의 지적이다.
목표: 봇의 답이 OWNER이 직접 쓴 것처럼, 그 채널 사람들처럼 자연스럽게 되도록 '앞으로 지킬 규칙'을 뽑는다.
- OWNER의 지적(critique)은 반드시 규칙으로 반영한다(이미 있는 규칙과 같으면 합친다).
- [OWNER 판정]은 OWNER이 봇 답을 직접 평가한 것이다. '나쁨'과 그 이유는 지적처럼 반영하고, 'OWNER 답'이 있으면 봇 답과의 차이(길이·말끝·내용)에서 규칙을 뽑는다. '좋음' 답은 지킬 본보기로 보고 그 답과 어긋나는 규칙은 만들지 않는다.
- 실제 사람 말과 봇 답의 차이(길이·말끝·줄임말·태도)에서 보이는 규칙을 더한다. 근거가 약하면 넣지 않는다.
- 규칙은 말투·태도·길이·내용 선택에 관한 것만. 안전 장치(보류, 먼저 안 보냄, 비공개 주제, AI냐는 질문에 거짓말 안 함)를 약하게 하는 규칙은 만들지 않는다.
- 채널: all(공통), kakao(갠톡), proxy(오픈채팅 고민방 대리 답). 채널별 최대 8개, 한 규칙 80자 이내, 구체적으로("~하지 말고 ~처럼").
- all에는 채널과 무관한 규칙만. 존댓말/반말·길이·줄임말처럼 채널마다 다른 말투 규칙은 해당 채널에만 넣는다(갠톡은 상대별 높임을 코드가 정하고, 고민방은 존댓말 한 문장).
- 아래 [고정 규칙]과 모순되는 규칙은 만들지 않는다. 예시 문구를 넣을 때도 고정 규칙에서 금지한 표현을 예시로 쓰지 않는다.
[고정 규칙]
- 고민방: 공감은 '속상하죠', '그럴 만해요', '당연한 거예요'처럼 짧은 인정 한 마디. '~하셨겠어요,', '힘들었겠어요', '마음이 느껴져요', '충분히 이해해요', '그랬군요' 같은 상담사·AI 어투 금지. 감정을 묻는 질문 금지. 정보 질문엔 공감 없이 바로 답.
- 갠톡: OWNER은 '~군요'를 쓰지 않는다. 보류 문구는 코드가 정하므로 바꾸라고 하지 않는다. 반말/존댓말은 [높임] 줄을 따른다.
- 공통: 'AI·봇' 소개 금지, 지어낸 경험·사실 금지, 이모지 거의 안 씀.
JSON만: {"rules": {"all": ["..."], "kakao": [], "proxy": []}, "why": "두 문장"}"""


def _unique_critiques(recs) -> list[dict]:
    """같은 지적(채널이 같고 공백을 정리한 글이 같음)은 한 번만. 가장 최근에 나온 자리에 남긴다(11/09: 같은 지적이 수십 번 쌓여 규칙을 끌고 간 일)."""
    out, seen = [], set()
    for r in reversed(list(recs)):
        t = " ".join(str(r.get("text") or "").split())
        k = (r.get("channel", "all"), t)
        if not t or k in seen:
            continue
        seen.add(k)
        out.append(dict(r, text=t))
    return out[::-1]


def reflect(run_claude=None) -> dict:
    """원장 → 규칙 추출 → 안전 검사 → learned_rules.json 갱신. 결과 요약을 돌려준다. 같은 지적은 입력에 한 번만 넣는다."""
    recs = _read_ledger()
    if not recs:
        return {"ok": False, "why": "원장 비어 있음"}
    crit = _unique_critiques(r for r in recs if r.get("source") == "owner" and r.get("kind") == "critique")
    bot = [r for r in recs if r.get("kind") == "bot_reply"][-80:]
    human = [r for r in recs if r.get("kind") == "human_chat"][-150:]
    cur = _load_rules()
    payload = ["[OWNER의 지적]"] + [f"- ({r.get('channel', 'all')}) {r['text']}" for r in crit[-40:]]
    judges = _unique_judges(recs)[-30:]
    payload += ["[OWNER 판정]"] + [
        f"- ({r.get('channel', 'all')}) {'좋음' if r.get('verdict') == 'good' else '나쁨'}: 봇 '{str(r.get('bot', ''))[:120]}'"
        + (f" / OWNER 답 '{str(r.get('owner'))[:100]}'" if r.get("owner") else "")
        + (f" / 이유: {str(r.get('reason'))[:120]}" if r.get("reason") else "") for r in judges]
    payload += ["[지금 규칙]"] + [f"- ({c}) {x['text']}" for c in CHANNELS for x in cur.get("rules", {}).get(c, [])]
    payload += ["[봇이 보낸 답]"] + [f"- ({r['source']}) {r.get('text', '')[:120]}" for r in bot]
    payload += ["[실제 사람들이 쓴 말]"] + [f"- ({r['source']}) {r.get('text', '')[:80]}" for r in human]
    text = "\n".join(payload)[:30000]
    out = (run_claude or _claude)(text, REFLECT_SYSTEM)
    try:
        d = json.loads(out[out.find("{"): out.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError, AttributeError):
        return {"ok": False, "why": "규칙 추출 실패"}
    new = {c: [] for c in CHANNELS}
    rejected = []
    for c in CHANNELS:
        for x in (d.get("rules") or {}).get(c, [])[:12]:
            x = str(x).strip()[:120]
            if not x:
                continue
            if UNSAFE.search(x):
                rejected.append({"channel": c, "text": x, "why": "안전 장치 해제"})
                continue
            bad_ex = _bad_example(x, c)
            if bad_ex:
                rejected.append({"channel": c, "text": x, "why": f"금지 표현을 예시로 씀({bad_ex})"})
                continue
            if c == "all" and REGISTER_RE.search(x):
                rejected.append({"channel": c, "text": x, "why": "말투 규칙은 공통에 넣지 않음"})
                continue
            if x not in [y["text"] for y in new[c]]:
                new[c].append({"text": _mask(x), "since": dt.datetime.now(KST).strftime("%m/%d")})
    data = {"updated_at": dt.datetime.now(KST).isoformat(timespec="seconds"), "rules": new,
            "rejected": (cur.get("rejected", []) + rejected)[-30:], "why": str(d.get("why", ""))[:300],
            "inputs": {"critiques": len(crit), "judges": len(judges), "bot_replies": len(bot), "human_lines": len(human)}}
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = RULES.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(RULES)
    add("system", kind="reflect", n={c: len(v) for c, v in new.items()}, rejected=len(rejected))
    return {"ok": True, "n": {c: len(v) for c, v in new.items()}, "rejected": rejected, "why": data["why"]}


def _claude(text: str, system: str) -> str:
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    exe = shutil.which(cfg["claude_cmd"]) or cfg["claude_cmd"]
    cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--tools", "",
           "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence", "--model", cfg["model"],
           "--system-prompt", system]
    msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    p = subprocess.run(cmd, input=json.dumps(msg, ensure_ascii=False) + "\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=BASE / "claude-cwd", timeout=300,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result" and not ev.get("is_error"):
            return ev.get("result") or ""
    return ""


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "reflect":
        print(json.dumps(reflect(), ensure_ascii=False, indent=1))
    elif cmd == "critique":
        ch = sys.argv[sys.argv.index("--channel") + 1] if "--channel" in sys.argv else "all"
        body = " ".join(a for a in sys.argv[2:] if a not in ("--channel", ch))
        critique(body, ch)
        print("기록함")
    else:
        for c in CHANNELS:
            print(c, "\n" + (rules_block(c) or "(없음)"))
