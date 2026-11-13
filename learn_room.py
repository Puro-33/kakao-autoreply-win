"""오픈채팅방 대화 학습(읽기 전용, 발신 0): 방의 말투·길이·은어·질문/답 흐름을 뽑아 ontology/rooms/<chat_id>.json·.md 로 저장.
봇은 이 프로필을 그 방에서 답할 때 프롬프트에 넣는다(room_profile_block).

- 닉네임·user_id는 저장하지 않는다(익명 '사람A'도 안 씀). 문장은 mask_sensitive + never_store 통과분만.
- 실행: py -3.12 -I learn_room.py <chat_id> [--no-llm]
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import re
import statistics
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.stdout.reconfigure(encoding="utf-8")

import logging  # noqa: E402
logging.disable(logging.CRITICAL)
import bot  # noqa: E402
import style_profile as SP  # noqa: E402

OUT = BASE / "ontology" / "rooms"
KST = dt.timezone(dt.timedelta(hours=9))
Q_RE = re.compile(r"\?|나요|까요|ㄴ가요|인가요|있나요|되나요|어때요|어떤가요|뭐예요|뭔가요|궁금|알려주|추천")
MARKS = {k: re.compile(v) for k, v in SP.MIRROR_FEATURES.items()}
HANGUL_WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")
STOP = set("그냥 진짜 근데 저도 저는 제가 이거 그거 저거 그럼 아니 근데요 혹시 지금 다들 그래서 너무 많이 좀 완전 이제 거의 아직 요즘 그게 이게 하는 있는 없는 같은 같아요 하고 해서 있어요 없어요 했는데 하는데 있나요 맞아요 네네 감사합니다".split())


def pull(chat_id: str) -> list[dict]:
    rows = bot.db("SELECT _id, id, user_id, type, scope, thread_id, message, v, created_at FROM chat_logs "
                  "WHERE chat_id = ? ORDER BY _id", [chat_id])
    out = []
    for r in rows:
        if bot.to_int(r.get("type"), 0) not in bot.TEXT_TYPES:
            continue
        m = (r.get("message") or "").strip()
        if not m or m.startswith("{"):
            continue
        out.append({"id": str(r.get("id") or ""), "u": str(r.get("user_id")), "scope": str(r.get("scope") or "1"),
                    "thread": str(r.get("thread_id") or ""), "t": bot.to_int(r.get("created_at"), 0), "m": m})
    return out


def safe(m: str) -> str | None:
    if bot.ONT.never_hit(m):
        return None
    return bot.mask_sensitive(m).replace("\n", " ").strip()


def stats(msgs: list[dict], owner: str) -> dict:
    others = [x for x in msgs if x["u"] != owner]
    lens = [len(x["m"]) for x in others]
    reg = collections.Counter(SP.questioner_style(x["m"])["register"] for x in others if len(x["m"]) >= 4)
    marks = {k: round(sum(bool(p.search(x["m"])) for x in others) / max(1, len(others)), 3) for k, p in MARKS.items()}
    # 같은 사람이 끊어 보내는 줄 수(연속 묶음)
    bursts, cur, prev = [], 0, None
    for x in others:
        if x["u"] == prev and x["scope"] == "1":
            cur += 1
        else:
            if cur:
                bursts.append(cur)
            cur, prev = 1, x["u"]
    if cur:
        bursts.append(cur)
    words = collections.Counter(w for x in others for w in HANGUL_WORD.findall(x["m"]) if w not in STOP)
    endings = collections.Counter()
    for x in others:
        s = re.sub(r"[\sㅋㅎㅠㅜ~!?.…]+$", "", x["m"])
        if len(s) >= 2:
            endings[s[-2:]] += 1
    span = (max(x["t"] for x in msgs) - min(x["t"] for x in msgs)) / 3600 if msgs else 0
    return {
        "messages": len(others), "speakers": len({x["u"] for x in others}), "hours_covered": round(span, 1),
        "len_median": statistics.median(lens) if lens else 0,
        "len_p90": sorted(lens)[int(len(lens) * 0.9)] if lens else 0,
        "short_ratio_le15": round(sum(l <= 15 for l in lens) / max(1, len(lens)), 3),
        "register": dict(reg.most_common()),
        "marks_ratio": marks,
        "burst_median": statistics.median(bursts) if bursts else 0,
        "thread_ratio": round(sum(x["scope"] in ("2", "3") for x in others) / max(1, len(others)), 3),
        "question_ratio": round(sum(bool(Q_RE.search(x["m"])) for x in others) / max(1, len(others)), 3),
        "top_words": [w for w, _ in words.most_common(60)],
        "top_endings": [e for e, _ in endings.most_common(25)],
    }


def qa_pairs(msgs: list[dict], owner: str, limit: int = 40) -> list[dict]:
    """질문 → 다른 사람의 답(스레드 답글, 또는 3분 안의 본채팅 답) 쌍."""
    by_thread = collections.defaultdict(list)
    for x in msgs:
        if x["thread"]:
            by_thread[x["thread"]].append(x)
    pairs = []
    for i, q in enumerate(msgs):
        if q["u"] == owner or q["scope"] != "1" or not Q_RE.search(q["m"]) or len(q["m"]) < 6:
            continue
        ans = [a for a in by_thread.get(q["id"], []) if a["u"] != q["u"]]
        if not ans:
            for a in msgs[i + 1:i + 12]:
                if a["t"] - q["t"] > 180:
                    break
                if a["u"] != q["u"] and a["scope"] == "1" and len(a["m"]) >= 2:
                    ans = [a]
                    break
        if not ans:
            continue
        qs, as_ = safe(q["m"]), [safe(a["m"]) for a in ans[:3]]
        as_ = [a for a in as_ if a]
        if qs and as_:
            pairs.append({"q": qs[:200], "a": [a[:150] for a in as_], "via": "thread" if ans[0]["thread"] else "main"})
    return pairs[-limit:]


LLM_SYSTEM = """너는 오픈채팅방 분위기 분석가다. 아래는 한 방의 실제 대화 일부(개인정보 가림)와 통계다.
OWNER(계정 주인)이 이 방에서 '방 사람처럼' 자연스럽게 말하려면 무엇을 알아야 하는지 정리하라.
JSON만: {"what_room": "한 줄(방의 목적과 주 화제)",
 "tone": ["말투 규칙 4~7개, 구체적으로(예: '~용', '~ㅋㅋ'로 끝, 존댓말이지만 가벼움)"],
 "glossary": {"은어/약어": "뜻(대화에서 확실한 것만, 모르면 넣지 마라)"},
 "answer_style": ["질문에 사람들이 답하는 방식 3~5개(길이·근거·경험담 여부)"],
 "avoid": ["이 방에서 어색하거나 눈총받을 것 3~5개"],
 "sample_replies": ["이 방 말투로 된 짧은 답 예시 5개(실제 문장 그대로 베끼지 말고 비슷하게)"]}"""


def llm_summary(sample: list[str], st: dict) -> dict:
    text = "[통계]\n" + json.dumps({k: v for k, v in st.items() if k != "top_words"}, ensure_ascii=False) + \
           "\n[자주 쓰는 말]\n" + ", ".join(st["top_words"][:50]) + "\n[대화]\n" + "\n".join(sample)
    out = bot.run_claude([{"type": "text", "text": text[:28000]}], LLM_SYSTEM, False, timeout=300) or ""
    try:
        return json.loads(out[out.find("{"): out.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return {"error": "요약 실패", "raw": out[:300]}


# 이 방 화제(입시·시험 등)는 갠톡에 새면 안 된다(비공개 주제) → 예시·규칙에서 뺀다
DOMAIN_RE = re.compile(r"입시|시험|모의|기출|합격|불합|요강|현강|인강|수학|물리|화학|영어|토익|강사|학원|선생|문제|점수|컷|1차|2차|"
                       r"면접|전공|학점|과외|문항|풀이|풀었|정답|오답|난이도|등급|수능|대학|학교|교수|공부|과목|\d|원서|지원|갠톡|"
                       r"[A-Z]의\s|[가-힣]+대(?![가-힣])|학사|득점|고득점|학벌|스펙|재수|반수|입학|비용|가격|얼리버드")  # 방 화제·은어(갠톡으로 새면 안 되는 낱말). 내 방에 맞게 고치세요

DM_RHYTHM_SYSTEM = """너는 대화 코치다. 아래는 OWNER이 '대화가 자연스럽다'고 고른 오픈채팅방의 분석이다.
이걸 OWNER의 1:1 갠톡 답장에 옮길 '리듬 규칙'으로 바꿔라. 조건:
- 방의 화제(입시·시험·학원·과목·점수)나 은어는 절대 넣지 않는다. 대화 리듬·길이·말끝·반응 방식만.
- 반말/존댓말 자체는 정하지 않는다(상대별로 따로 정해짐). '존댓말일 때는 ~, 반말일 때는 ~'처럼 양쪽에 다 맞게 쓴다.
- '~군요'는 OWNER이 안 쓰므로 예시에 쓰지 않는다.
- 경험담·지어낸 사례를 덧붙이라는 규칙은 만들지 않는다(봇은 OWNER의 경험을 지어내면 안 됨).
- 6~8개, 한 규칙 70자 이내, 구체적으로('~하지 말고 ~처럼').
JSON만: {"rules": ["..."]}"""


def distill_dm_rhythm(prof: dict, msgs: list[dict], owner: str) -> dict:
    """갠톡에 옮길 리듬: 화제 없는 규칙 + 화제 없는 실제 짧은 주고받기."""
    ex = []
    for p in prof["qa_pairs"]:
        a = [x for x in p["a"] if not DOMAIN_RE.search(x) and not bot.ONT.topic_hit(x) and len(x) <= 40]
        if a and not DOMAIN_RE.search(p["q"]) and not bot.ONT.topic_hit(p["q"]) and len(p["q"]) <= 60:
            ex.append({"q": p["q"], "a": a[0]})
    # 질문쌍이 모자라면 본채팅의 연속 두 사람 주고받기에서 짧은 것을 더 뽑는다
    for x, y in zip(msgs, msgs[1:]):
        if len(ex) >= 14:
            break
        if x["u"] == y["u"] or owner in (x["u"], y["u"]) or y["t"] - x["t"] > 120 or x["scope"] != "1" or y["scope"] != "1":
            continue
        q, a = safe(x["m"]), safe(y["m"])
        if not q or not a or not (4 <= len(q) <= 40 and 2 <= len(a) <= 30) or not Q_RE.search(q):
            continue
        if DOMAIN_RE.search(q + a) or bot.ONT.topic_hit(q + a):
            continue
        if re.match(r"(근데|이면|그래서|그리고|아니면)", a):
            continue
        if {"q": q, "a": a} not in ex:
            ex.append({"q": q, "a": a})
    rules = []
    sm = prof.get("summary") or {}
    if sm and "error" not in sm:
        text = json.dumps({"tone": sm.get("tone"), "answer_style": sm.get("answer_style"), "avoid": sm.get("avoid"),
                           "stats": {k: prof["stats"][k] for k in ("len_median", "short_ratio_le15", "burst_median", "marks_ratio", "top_endings")}},
                          ensure_ascii=False)
        out = bot.run_claude([{"type": "text", "text": text}], DM_RHYTHM_SYSTEM, False, timeout=200) or ""
        try:
            rules = [r for r in json.loads(out[out.find("{"): out.rfind("}") + 1]).get("rules", [])
                     if isinstance(r, str) and not DOMAIN_RE.search(re.sub(r"\d", "", r)) and not bot.ONT.topic_hit(r)
                     and not re.search(r"경험|사례|저도 그랬|나도 그랬|주변|군요", r)][:8]
        except (ValueError, json.JSONDecodeError):
            rules = []
    return {"rules": rules, "examples": ex[-14:]}


def learn(chat_id: str, use_llm: bool = True) -> dict:
    owner = str(bot.iris("/config").get("bot_id") or bot.S.bot_id or "")
    msgs = pull(chat_id)
    st = stats(msgs, owner)
    pairs = qa_pairs(msgs, owner)
    sample = [s for s in (safe(x["m"]) for x in msgs if x["u"] != owner) if s][-350:]
    prof = {"chat_id": chat_id, "room": bot.room_label(chat_id), "rules": bot.room_rules(chat_id)[:300],
            "learned_at": dt.datetime.now(KST).isoformat(timespec="seconds"), "stats": st, "qa_pairs": pairs,
            "summary": llm_summary(sample, st) if use_llm else {}}
    prof["dm_rhythm"] = distill_dm_rhythm(prof, msgs, owner)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{chat_id}.json").write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / f"{chat_id}.md").write_text(render_md(prof), encoding="utf-8")
    return prof


def render_md(p: dict) -> str:
    st, sm = p["stats"], p.get("summary") or {}
    reg = ", ".join(f"{k} {v}" for k, v in st["register"].items())
    lines = [f"# {p['room']} 대화 학습 ({p['learned_at'][:16]})", "",
             f"- 메시지 {st['messages']}개, 말한 사람 {st['speakers']}명, {st['hours_covered']}시간치",
             f"- 길이 중앙값 {st['len_median']}자, 90%가 {st['len_p90']}자 이하, 15자 이하 비율 {st['short_ratio_le15']}",
             f"- 말투: {reg}", f"- 한 사람이 끊어 보내는 줄 수(중앙값) {st['burst_median']}, 스레드 비율 {st['thread_ratio']}, 질문 비율 {st['question_ratio']}",
             f"- 표시 사용 비율: " + ", ".join(f"{k} {v}" for k, v in st["marks_ratio"].items() if v),
             f"- 자주 쓰는 끝말: {' '.join(st['top_endings'][:15])}", ""]
    if sm and "error" not in sm:
        lines += [f"## 어떤 방", sm.get("what_room", ""), "", "## 말투"] + [f"- {x}" for x in sm.get("tone", [])]
        lines += ["", "## 은어"] + [f"- {k}: {v}" for k, v in (sm.get("glossary") or {}).items()]
        lines += ["", "## 답하는 방식"] + [f"- {x}" for x in sm.get("answer_style", [])]
        lines += ["", "## 피할 것"] + [f"- {x}" for x in sm.get("avoid", [])]
        lines += ["", "## 이 방 말투 예시(지어낸 것)"] + [f"- {x}" for x in sm.get("sample_replies", [])]
    dr = p.get("dm_rhythm") or {}
    lines += ["", "## 갠톡에 옮기는 리듬(화제·은어 뺌)"] + [f"- {x}" for x in dr.get("rules", [])]
    lines += ["", "### 화제 없는 실제 주고받기"] + [f"- {e['q']} → {e['a']}" for e in dr.get("examples", [])]
    lines += ["", f"## 질문→답 예시 ({len(p['qa_pairs'])}쌍 중 10)"]
    for q in p["qa_pairs"][-10:]:
        lines.append(f"- Q: {q['q'][:80]}  \n  A: {' / '.join(a[:60] for a in q['a'])}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    cid = sys.argv[1]
    p = learn(cid, use_llm="--no-llm" not in sys.argv)
    print(render_md(p))
