"""OWNER 말투 프로필 (사용자 지시 2026-11-08: 앞으로 보낼 모든 답장을 최대한 자연스러운 내 말투로).

OWNER이 실제로 보낸 카톡(봇이 보내기 시작한 2026-11-07 이전 것만)에서
- 말투(반말·해요체·합쇼체·혼용)별 통계: 길이, ㅋ·ㅠ·~·!·? 빈도, 자주 쓰는 끝말, 끊어 보내는 비율, 이모지 빈도
- 상대별 실제 주고받은 쌍(상대 말 → OWNER 답). 그 상대에게 답할 때만 쓴다.
- 말투별 OWNER 문장 예시(OWNER 문장만, 다른 사람 말은 없음). 상대별 쌍이 부족할 때 쓴다.
를 뽑아 ontology/style.json에 저장하고, 답할 때 넣을 [내 말투] 블록을 만든다.
카톡 발신 코드 없음(DB 읽기 함수는 호출하는 쪽이 넘긴다).
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import re
import statistics
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
STYLE_FILE = BASE / "ontology" / "style.json"
KST = dt.timezone(dt.timedelta(hours=9))
BOT_SEND_START = int(dt.datetime(2026, 11, 7, tzinfo=KST).timestamp())
TEXT_TYPES = ("1", "26")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
CATEGORY_REGISTER = {"친구": "반말", "교수": "합쇼체", "가족": "반말"}
URL_RE = re.compile(r"https?://\S+")


def _register_of(store, chat_id: str, category: str) -> str:
    for f in store.data["facts"].values():
        if f["subject"] == f"c:{chat_id}" and f["predicate"] == "honorific_mine" and f["status"] == "active":
            return f["value"]
    return CATEGORY_REGISTER.get(category, "해요체")


def _usable(text: str, store) -> bool:
    """예시로 써도 되는 OWNER 문장인가: 비공개 주제·개인정보·링크·긴 코드 없음, 3~80자."""
    t = (text or "").strip()
    if not 2 <= len(t) <= 80 or "\n" in t[:80] and len(t.splitlines()) > 3:
        return False
    if URL_RE.search(t) or store.topic_hit(t) or store.never_hit(t):
        return False
    if re.search(r"[{}<>=;]|def |import |\.py|\.exe", t):
        return False
    return True


def build(db, me_id: str, rooms: dict, store, max_rows: int = 600) -> dict:
    """rooms: {chat_id: {"name", "category"}}. OWNER 메시지로 프로필을 만들어 저장하고 돌려준다."""
    per_reg: dict[str, list[str]] = collections.defaultdict(list)
    bursts: dict[str, list[int]] = collections.defaultdict(list)
    pairs: dict[str, list[list[str]]] = {}
    acks: dict[str, list[str]] = {}
    per_contact: dict[str, list[str]] = {}
    names = [c.get("name") or "" for c in store.data["contacts"].values() if len(c.get("name") or "") >= 2]
    for cid, r in rooms.items():
        cat = r.get("category", "미분류")
        reg = _register_of(store, cid, cat)
        rows = db("SELECT _id, user_id, type, message, v, created_at FROM chat_logs WHERE chat_id = ? "
                  "ORDER BY _id DESC LIMIT ?", [cid, max_rows])
        rows = [x for x in reversed(rows) if str(x.get("type")) in TEXT_TYPES and (x.get("message") or "").strip()]
        run, last_t, last_other = 0, 0, None
        cp = []
        for x in rows:
            t = int(x.get("created_at") or 0)
            mine = str(x.get("user_id")) == str(me_id)
            msg = (x.get("message") or "").strip()
            if mine and t >= BOT_SEND_START:
                run, last_other = 0, None
                continue          # 봇이 보냈을 수 있는 말은 말투 근거에서 뺀다
            if mine:
                per_reg[reg].append(msg)
                per_contact.setdefault(cid, []).append(msg)
                if run and t - last_t >= 90:
                    bursts[reg].append(run)
                    run = 0
                run += 1
                if last_other and t - last_other[0] < 1800 and _usable(msg, store) and _usable(last_other[1], store):
                    cp.append([store.mask(last_other[1])[:120], store.mask(msg)[:120]])
                    last_other = None
                last_t = t
            else:
                if run:
                    bursts[reg].append(run)
                run = 0
                last_other = (t, msg)
        if run:
            bursts[reg].append(run)
        if cp:
            pairs[cid] = cp[-10:]
        short = [m for m in per_contact.get(cid, []) if len(m) <= 10 and _usable(m, store)]
        if short:
            acks[cid] = [m for m, _ in collections.Counter(short).most_common(8)]
    regs = {}
    for reg, ms in per_reg.items():
        lens = sorted(len(m) for m in ms)
        if not lens:
            continue

        def rate(p, _ms=ms):
            return round(sum(bool(re.search(p, m)) for m in _ms) / len(_ms), 2)
        ends = collections.Counter()
        for m in ms:
            core = re.sub(r"[\sㅋㅎㅠㅜ~!?.^]+$", "", m)
            if len(core) >= 2:
                ends[core[-2:]] += 1
        b = bursts.get(reg, [])
        examples = [m for m in ms if _usable(m, store) and not any(n in m for n in names)]
        regs[reg] = {
            "n": len(ms), "len_med": int(statistics.median(lens)), "len_p90": lens[int(len(lens) * 0.9)],
            "rates": {"ㅋ": rate(r"ㅋ"), "ㅎ": rate(r"ㅎ"), "ㅠ": rate(r"[ㅠㅜ]"), "~": rate(r"~"), "!": rate(r"!"),
                      "?": rate(r"\?"), "이모지": round(sum(bool(EMOJI_RE.search(m)) for m in ms) / len(ms), 2),
                      "마침표": rate(r"\.$")},
            "ends": [e for e, _ in ends.most_common(10)],
            "burst_ratio": round(sum(x >= 2 for x in b) / max(1, len(b)), 2),
            "examples": [store.mask(m) for m in examples[-14:]],
        }
    contact_style = {}
    for cid, ms in per_contact.items():
        if len(ms) >= 3:
            ban = sum(bool(re.search(r"(어|아|야|지|해|자|냐|니|게|래|까|ㅇㅇ|ㅋ)[\s?!.~ㅋㅎㅠ]*$", m)) and
                      not re.search(r"(요|니다|세요|까요)[\s?!.~ㅋㅎㅠ]*$", m) for m in ms) / len(ms)
            contact_style[cid] = {"n": len(ms), "len_med": int(statistics.median(len(m) for m in ms)),
                                  "banmal_ratio": round(ban, 2)}
    prof = {"built_at": dt.datetime.now(KST).isoformat(timespec="seconds"), "registers": regs, "pairs": pairs,
            "acks": acks, "contacts": contact_style}
    STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STYLE_FILE.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    return prof


_CACHE: dict = {"mtime": 0.0, "prof": None}


def load() -> dict | None:
    try:
        m = STYLE_FILE.stat().st_mtime
    except OSError:
        return None
    if m != _CACHE["mtime"]:
        try:
            _CACHE["prof"] = json.loads(STYLE_FILE.read_text(encoding="utf-8"))
            _CACHE["mtime"] = m
        except (OSError, json.JSONDecodeError):
            return None
    return _CACHE["prof"]


def register_for(store, chat_id: str, category: str) -> str:
    return _register_of(store, chat_id, category)


def style_block(chat_id: str, register: str, include_pairs: bool = True) -> str:
    """[내 말투] 블록: 통계를 사람 말로 + 실제 예시. 프로필이 없으면 빈 문자열."""
    prof = load()
    if not prof:
        return ""
    regs = prof.get("registers", {})
    r = regs.get(register) or regs.get("혼용") or next(iter(regs.values()), None)
    if not r:
        return ""
    rt = r["rates"]

    def freq(x):
        return "자주" if x >= 0.25 else "가끔" if x >= 0.08 else "거의 안"
    lines = [f"[내 말투 — OWNER이 이 말투({register})로 실제 보낸 카톡 {r['n']}개에서 잰 것. 이대로 쓴다]",
             f"- 길이: 보통 {r['len_med']}자 안팎, 길어도 {r['len_p90']}자. 이보다 길게 쓰지 않는다.",
             f"- ㅋ {freq(rt['ㅋ'])} · ㅠ {freq(rt['ㅠ'])} · ~ {freq(rt['~'])} · ! {freq(rt['!'])} · ? {freq(rt['?'])} 씀. "
             f"이모지는 {('거의 안 씀 → 쓰지 않는다' if rt['이모지'] < 0.05 else '가끔')}. 문장 끝 마침표 {freq(rt['마침표'])}.",
             f"- 자주 쓰는 끝말: {', '.join(r['ends'][:8])}"]
    if r["burst_ratio"] >= 0.2:
        lines.append(f"- 짧게 끊어 여러 번 보내는 편(대화의 {int(r['burst_ratio'] * 100)}%). 두세 마디면 줄바꿈으로 나눠 쓴다(줄마다 따로 보냄).")
    else:
        lines.append("- 한 번에 한 메시지로 보낸다(줄바꿈으로 나누지 않는다).")
    cs = prof.get("contacts", {}).get(chat_id) if include_pairs else None
    if cs:
        mode = "반말" if cs["banmal_ratio"] >= 0.6 else "존댓말" if cs["banmal_ratio"] <= 0.25 else "반말·존댓말 섞어"
        lines.append(f"- 이 상대에게는 {mode}로 썼다(실제 {cs['n']}개, 보통 {cs['len_med']}자). 범주 말투 규칙과 다르면 이것을 따른다.")
    acks = (prof.get("acks", {}).get(chat_id) or []) if include_pairs else []
    if acks:
        lines.append(f"- 이 상대에게 쓰던 짧은 맞장구: {' / '.join(acks)} — 정보·안내·공유만 받았으면 이 중 하나 정도로 짧게 끝낸다.")
    pairs = (prof.get("pairs", {}).get(chat_id) or []) if include_pairs else []
    if pairs:
        lines.append("[이 상대와 실제로 주고받은 말 — 상대 말 → OWNER 답. 길이·말투를 그대로 따르고, 내용은 지금 대화에 맞게]")
        lines += [f"- {a} → {b}" for a, b in pairs[-8:]]
    ex = r.get("examples") or []
    if ex:
        lines.append("[OWNER이 이 말투로 쓴 실제 문장 예시 — 말투만 참고]")
        lines += [f"- {e}" for e in ex[-8:]]
    return "\n".join(lines)


# 말투 지시의 단 하나의 기준(11/09 [6]): 표시·호칭·반말/존댓말·길이는 상대별 통계만 정한다.
# 리듬 블록(rhythm_block)·글 리듬(voice_corpus)·피드백 규칙에서는 이 지시를 strip_style_directives로 걷어 낸다.
STYLE_AUTHORITY = ("[말투 기준 — 말투 지시가 서로 다르면 이 줄을 따른다] ㅋ·ㅎ·ㅠ·~·!·이모지 같은 표시, 호칭, 반말/존댓말, "
                   "글자 길이는 [내 말투](이 상대와 실제로 주고받은 말·상대별 통계)와 상대 카드의 말투 기록만 따른다. "
                   "호칭은 그 기록에 있던 대로만 쓰고 없으면 붙이지 않는다. 다른 블록은 이것들을 정하지 않는다. "
                   "예외는 OWNER이 직접 적은 [교정 사항]뿐이다.")

VOICE_RULES = """[대신 답할 때 공통 규칙 — OWNER이 직접 쓴 것처럼]
- 너는 OWNER 대신 OWNER으로서 쓴다. 'AI·봇·비서·클로드'라고 소개하지 않고, OWNER을 3인칭("OWNER이 ~")으로 부르지 않는다. 미룰 때도 "확인하고 알려줄게"처럼 1인칭으로.
- """ + STYLE_AUTHORITY + """
- 팁·설명은 제일 쓸모 있는 것 하나만, 나열하지 않는다.
- 상대가 정보·안내·공유·확인만 했으면 이 상대에게 쓰던 짧은 맞장구 하나로 끝낸다. "알려주셔서 감사합니다", "참고해서 해볼게요", "좋은 팁 감사합니다" 같은 정형 문구는 쓰지 않는다.
- 가벼운 응원·맞장구·리액션·짧은 의견("들어볼까?" → "ㅎㅇㅌ!", "이거 들었어?" → "오호")은 근거가 없어도 답한다(confidence 0.7 이상). 사실을 새로 지어내지만 않으면 된다.
  보류는 약속·시간 정하기, 돈, 결정(지원·가입·수락), OWNER만 아는 사실(경험·계획·상황)이 필요한 것만.
- 상대 말을 되풀이해 요약하지 않는다("~하시는군요", "~가 중요하군요").
- 상담 템플릿("많이 힘드셨겠어요", "~하는 것도 좋은 선택이에요"), 도우미식 마무리("더 알려주시면~", "도움이 됐으면 좋겠어요")를 쓰지 않는다.
- OWNER이 실제로 겪었다고 근거가 없는 경험담·사실은 지어내지 않는다. 시각·날짜는 대화 기록의 시각으로만 말하고 모르면 말하지 않는다. 모르면 짧게 모른다고 한다.
- 힘든 고민에는 조언보다 짧은 공감을 먼저. 따돌림·폭행·자해 같은 위험 신호면 공감 한 줄 + 1388(문자 가능)·믿을 만한 어른 안내, 지금 위험하면 112."""
# "AI야?" 보류 규칙은 reply_policy.md '답을 쓸 때'에 같은 문장이 있어 여기서는 뺐다(11/09 중복 정리).


# ───────── 말투 검사(코드): 초안에 AI 티가 있으면 한 번 다시 쓰게 한다 ─────────
LINT_PATTERNS = [
    (r"(알려|말씀해|공유해|설명해|답해)\s?주셔서\s?(정말\s?|너무\s?)?(감사|고맙)", "정형 감사 문구"),
    (r"참고해\s?(볼게|보겠|할게)|좋은\s?(팁|정보)\s?(감사|고마)", "정형 마무리"),
    (r"(하시|이시|되시|가|이|하|는|었|았)군요|중요하군요", "상대 말 되풀이(~군요)"),
    (r"도움이?\s?(됐|되었|되셨)으면|더\s?(알려|말씀해)\s?주시면|언제든(지)?\s?(말씀|연락|물어)", "도우미식 마무리"),
    (r"(많이|정말)\s?(힘드셨|속상하셨|지치셨)겠", "상담 템플릿"),
]


def lint(reply: str, chat_id: str, register: str) -> list[str]:
    """OWNER 말투에서 벗어난 점 목록(없으면 빈 목록)."""
    issues = []
    r = reply or ""
    for p, why in LINT_PATTERNS:
        if re.search(p, r):
            issues.append(why)
    prof = load() or {}
    if "군요" in r and "상대 말 되풀이(~군요)" not in issues and not _uses(prof, "군요"):
        issues.append("OWNER이 안 쓰는 '~군요'")
    reg = (prof.get("registers") or {}).get(register) or {}
    # 11/11: 말투 통계가 없어도 이모지·링크 아이콘(👍 🔗 등)은 잡는다(OWNER은 거의 안 씀, 통계로 많이 쓰는 상대만 예외)
    if reg.get("rates", {}).get("이모지", 0) < 0.05 and EMOJI_RE.search(r):
        issues.append("이모지(OWNER은 거의 안 씀)")
    if reg:
        cs =(prof.get("contacts") or {}).get(chat_id) or {}
        med = cs.get("len_med") or reg.get("len_med") or 30
        limit = max(reg.get("len_p90", 80), med * 2.5)
        if len(r.replace("\n", "")) > limit:
            issues.append(f"너무 김({len(r)}자, 이 상대에게 보통 {med}자)")
    return issues


def _uses(prof: dict, token: str) -> bool:
    """OWNER 실제 문장(예시·맞장구·주고받은 말)에 이 표현이 있는가."""
    for reg in (prof.get("registers") or {}).values():
        if any(token in e for e in reg.get("examples") or []):
            return True
    for ps in (prof.get("pairs") or {}).values():
        if any(token in b for _a, b in ps):
            return True
    return any(token in a for acks in (prof.get("acks") or {}).values() for a in acks)


def default_ack(chat_id: str, register: str) -> str:
    prof = load() or {}
    acks = (prof.get("acks") or {}).get(chat_id) or []
    short = [a for a in acks if len(a) <= 6 and not re.search(r"\d", a)]
    if short:
        return short[0]
    return {"반말": "ㅇㅋ", "합쇼체": "넵!", "혼용": "넵"}.get(register, "넵~")


def scrub(reply: str, chat_id: str, register: str) -> str:
    """다시 써도 남은 AI 티 문장을 잘라 낸다. 남는 게 없으면 이 상대에게 쓰던 맞장구로."""
    prof = load() or {}
    pats = [p for p, _ in LINT_PATTERNS]
    if not _uses(prof, "군요"):
        pats.append(r"군요")
    parts = re.split(r"(?<=[.!?~])\s+|\n", reply.strip())
    keep = [x for x in parts if x.strip() and not any(re.search(p, x) for p in pats)]
    out = " ".join(keep).strip()
    if not out or len(re.sub(r"[\s.!?~ㅋㅎㅠㅜ]", "", out)) < 2:
        return default_ack(chat_id, register)
    return out


# ───────── 글쓴이 거울(사용자 지시 11/08: 다른 답글은 AI일 수 있으니, 확실히 사람인 글쓴이 말투에 맞춘다) ─────────
_POLITE_END = re.compile(r"(요|죠|니다|니까|세요|까요|나요|데요|네요|어요|아요|해요|에요|예요|래요|용|욤|여|염)[\s?!.~ㅋㅎㅠㅜ;^]*$")
_CUTE_END = re.compile(r"(용|욤|여|염|당|댱|쪄|숑)[\s?!.~ㅋㅎㅠㅜ^]*$")
_CHOSUNG = re.compile(r"\b[ㄱ-ㅎ]{2,}\b")
MIRROR_FEATURES = {"ㅋ": r"ㅋ", "ㅎ": r"ㅎ{2,}", "ㅠ": r"[ㅠㅜ]", "~": r"~", "!": r"!", "?": r"\?", "…": r"\.\.|…",
                   "이모지": r"[\U0001F300-\U0001FAFF\u2600-\u27BF]",
                   "초성": r"(?<![가-힣ㄱ-ㅎ])(?![ㅋㅎ]+(?![ㄱ-ㅎ]))[ㄱ-ㅎ]{2,}(?![가-힣])"}   # ㅋㅋ·ㅎㅎ는 초성체로 안 셈


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?~ㅠㅜㅋ])\s+|\n+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def questioner_style(text: str) -> dict:
    """글쓴이 글의 말투: 존댓말/반말, 귀여운 말끝(~용·~여), 문장 길이, ㅋ·ㅠ·~ 등 쓰는 표시."""
    sents = _sentences(text) or [text or ""]
    polite = sum(bool(_POLITE_END.search(x)) for x in sents) / max(1, len(sents))
    cute = sum(bool(_CUTE_END.search(x)) for x in sents) / max(1, len(sents))
    feats = {k: bool(re.search(p, text or "")) for k, p in MIRROR_FEATURES.items()}
    lens = sorted(len(x) for x in sents)
    return {"register": "존댓말" if polite >= 0.5 else "반말", "cute": cute >= 0.3, "feats": feats,
            "sent_len": lens[len(lens) // 2] if lens else 0, "n_sent": len(sents)}


def mirror_block(text: str) -> str:
    """대리 답 프롬프트에 넣을 [글쓴이 말투에 맞추기] 지시."""
    st = questioner_style(text)
    used = [k for k, v in st["feats"].items() if v and k not in ("?",)]
    unused = [k for k in ("ㅋ", "ㅠ", "~", "이모지", "초성") if not st["feats"].get(k)]
    target = max(10, min(45, int(st["sent_len"] * 0.8) or 20))
    lines = ["[글쓴이 말투에 맞추기 — 다른 답글이 아니라 이 글쓴이(사람)의 말투를 기준으로 한다]",
             f"- 글쓴이는 {st['register']}" + ("(~용·~여 같은 말끝)" if st["cute"] else "") + f"로 썼다. 답도 {st['register']}로."
             + (" 말끝을 조금 부드럽게(~요, ~용 정도는 괜찮다)." if st["cute"] else ""),
             f"- 글쓴이 한 문장은 보통 {st['sent_len']}자. 답은 한 문장 {target}자 안팎, 많아야 두 문장."]
    if used:
        lines.append(f"- 글쓴이가 쓴 표시: {', '.join(used)} — 답에도 한 번 정도 자연스럽게 섞어도 된다.")
    if unused:
        lines.append(f"- 글쓴이가 안 쓴 표시: {', '.join(unused)} — 답에도 쓰지 않는다.")
    return "\n".join(lines)


def mirror_score(post: str, reply: str) -> dict:
    """답이 글쓴이 말투를 얼마나 따랐나: 존댓말/반말 일치, 길이 비율(답 한 문장 / 글쓴이 한 문장), 표시 일치율."""
    a, b = questioner_style(post), questioner_style(reply)
    keys = [k for k in MIRROR_FEATURES if k != "?"]
    agree = sum(a["feats"][k] == b["feats"][k] for k in keys) / len(keys)
    ratio = (b["sent_len"] / a["sent_len"]) if a["sent_len"] else 0
    return {"register_ok": a["register"] == b["register"], "len_ratio": round(ratio, 2), "feat_agree": round(agree, 2),
            "extra_marks": [k for k in keys if b["feats"][k] and not a["feats"][k]]}



# ───────── 말투 지시 걸러내기: STYLE_AUTHORITY가 정하는 것(표시·호칭·반말/존댓말·길이·끊어 보내기)을 다른 블록에서 뺀다 ─────────
_QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"|‘([^’]*)’|“([^”]*)”")
_MARK_ONLY = re.compile(r"^[\sㅋㅎㅠㅜ~!…\.]+$")
_MARK_WORD = re.compile(r"ㅋ|ㅠ|ㅜ|ㅎㅎ|이모지|이모티콘|느낌표|물결|…|!|~(?![가-힣\d])|☐")
_HONOR_WORD = re.compile(r"호칭")
_LEN_WORD = re.compile(r"\d+\s*자(?!리)")
_REG_DEFER = re.compile(r"(반말|존댓말)[^.]{0,30}(우선|따르|따른|따라)")
_BURST_WORD = re.compile(r"여러 줄|줄바꿈|끊어 (여러 번 )?보내|한 번에 한 (줄|메시지)")
_THANKS_MUST = re.compile(r"꼭 한마디|(감사|고마)[^.]{0,40}(꼭|붙이기|붙여)")   # 정형 감사 금지(VOICE_RULES)와 부딪힘


def _unquoted(s: str) -> str:
    """따옴표 속 예시는 지우되, 표시만 든 예시('ㅠㅠ', '…')는 표시 지시로 남긴다(☐)."""
    return _QUOTED.sub(lambda m: " ☐ " if _MARK_ONLY.match(next(g for g in m.groups() if g is not None) or " ")
                       else " ", s)


def strip_style_directives(lines: list[str]) -> list[str]:
    """표시·호칭 지시가 든 줄은 통째로, 길이·끊어 보내기·'반말/존댓말은 ~를 따른다' 문장은 그 문장만 뺀다."""
    out = []
    for line in lines or []:
        u = _unquoted(line)
        if _MARK_WORD.search(u) or _HONOR_WORD.search(u) or _THANKS_MUST.search(line):
            continue
        sents = re.split(r"(?<=\.)\s+", line.strip())
        keep = [s for s in sents if not (_LEN_WORD.search(_unquoted(s)) or _BURST_WORD.search(_unquoted(s))
                                         or _REG_DEFER.search(_unquoted(s)))]
        t = " ".join(keep).strip()
        bare = re.sub(r"존댓말은|반말은|처럼|[\s.,/☐]", "", _unquoted(t))   # 예시만 남은 줄은 버린다
        if len(bare) >= 4:
            out.append(t)
    return out


def style_conflicts(prompt: str) -> list[str]:
    """완성된 프롬프트에서 서로 반대되는 말투 지시 쌍을 찾는다(테스트·점검용). 따옴표 속 예시는 보지 않는다."""
    def _q(m):
        g = next((x for x in m.groups() if x is not None), "")
        return g if g and _MARK_ONLY.match(g) else " "   # 'ㅠㅠ' 같은 표시만 든 예시는 지시로 본다
    text = _QUOTED.sub(_q, prompt)
    probs = []
    for mark, pat in (("ㅋ", r"ㅋ+"), ("ㅠ", r"[ㅠㅜ]+"), ("이모지", r"이모지"), ("!", r"!|느낌표"), ("~", r"~(?![가-힣\d])|물결")):
        neg = re.search(rf"({pat})[^\n.]{{0,40}}(쓰지 않|안 쓰|쓰지 말|쓰지 마|붙이지 않|빼고|거의 없|금지)", text)
        pos = re.search(rf"({pat})[^\n.]{{0,40}}(꼭|자주 쓴다|붙여|붙인다|붙이기|넣어|써라|써(?![가-힣])|위주|섞어|때만|로[.,])", text)
        if neg and pos:
            probs.append(f"{mark}: '{neg.group(0)}' ↔ '{pos.group(0)}'")
    if len(re.findall(r"\[말투 기준 —", prompt)) > 1:
        probs.append("말투 기준이 두 번 이상")
    for p in (r"닉네임에 '님'|님'을 붙여", r"AI 도우미", r"(대개|보통) ?3~5문장"):
        if re.search(p, prompt):
            probs.append(f"비서용 지시: {p}")
    return probs


ROOMS_DIR = BASE / "ontology" / "rooms"   # 테스트가 바꿔 끼울 수 있게


def rhythm_block(room_ids: list[str]) -> str:
    """OWNER이 '자연스럽다'고 고른 방에서 배운 대화 리듬(ontology/rooms/<id>.json의 dm_rhythm). 화제·은어는 이미 뺀 상태."""
    rules, ex = [], []
    for rid in room_ids or []:
        p = ROOMS_DIR / f"{rid}.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8")).get("dm_rhythm") or {}
        except (OSError, ValueError):
            continue
        rules += d.get("rules", [])
        ex += d.get("examples", [])
    rules = strip_style_directives(rules)   # 표시·호칭·길이는 [말투 기준]만 정한다
    if not rules and not ex:
        return ""
    out = ["[자연스러운 대화 리듬 — OWNER이 고른 방에서 배움. 말끝·묻는 법·마무리만 참고. 그 방의 화제나 예시 문장을 그대로 가져오지 않는다]"]
    out += [f"- {r}" for r in rules[:8]]
    if ex:
        out.append("주고받기 예(리듬만 참고):")
        out += [f"  {e['q']} → {e['a']}" for e in ex[:10]]
    return "\n".join(out)


# ───────── 높임(11/09 round2 문제 3): 말끝으로 반말·해요체·합쇼체를 가른다 ─────────
# 예전 휴리스틱은 '…ㅜㅜ'·'ㅋㅋ' 같은 꼬리를 반말로 잘못 셌다 → 꼬리 표시를 떼고 말끝만 본다. 모르는 끝(명사·'잠깐만')은 ""(안 셈)
_REG_TAIL_RE = re.compile(r"[\s.,!?~ㅋㅎㅠㅜ;:^…><)(\-_*+=♡♥☆★'\"\U0001F300-\U0001FAFF☀-➿]+$")
_HASIPSHO_END_RE = re.compile(r"(?:니다|니까|십시오|습니다|습니까|ㅂ니다|십니까|시죠|십쇼)$")
_HAEYO_END_RE = re.compile(r"(?:요|용|욤|죠|죵|여|염|세용|에염)$")
_POLITE_WORD_RE = re.compile(r"^(?:네|넵|넹|넴|예|네네|넵넵|네엡|옙|앗\s?네|아\s?네|네\s?넵)$")
_BANMAL_WORD_RE = re.compile(r"^(?:응|웅|ㅇㅇ|ㅇㅋ|ㄱㄱ|ㄴㄴ|ㅇㅈ|ㄹㅇ|어|엉|그래|아니|아냐|오키|오케이?|ㅇㅋㅇㅋ)$")
_BANMAL_LAST = set("어아야지해자냐니게께래까다네든데걸임음함됨줘봐와거고듯나대군셈랑구여")   # '여'는 아래에서 해요체로 먼저 셈


def sentence_register(s: str) -> str:
    """한 문장의 높임: '합쇼체' / '해요체' / '반말' / ""(말끝으로 모름)."""
    core = _REG_TAIL_RE.sub("", (s or "").strip())
    if not core:
        return ""
    if _POLITE_WORD_RE.match(core):
        return "해요체"
    if _BANMAL_WORD_RE.match(core):
        return "반말"
    if _HASIPSHO_END_RE.search(core):
        return "합쇼체"
    if _HAEYO_END_RE.search(core):
        return "해요체"
    if re.search(r"[ㄱ-ㅎ]{2,}$", core):   # 'ㅇㅇ'·'ㄱㄱ'로 끝남
        return "반말" if re.search(r"(?:ㅇㅇ|ㅇㅋ|ㄱㄱ|ㄴㄴ|ㅇㅈ|ㄹㅇ)$", core) else ""
    return "반말" if core[-1] in _BANMAL_LAST else ""


def _reg_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?~ㅠㅜㅋㅎ…])\s+|\n+|(?<=[.!?])(?=[가-힣])", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def text_register(text: str) -> str:
    """글 전체의 높임: '합쇼체' / '해요체' / '반말' / '섞임' / ""(모름). 존댓말 문장이 하나라도 합쇼체면 합쇼체."""
    regs = [x for x in (sentence_register(p) for p in _reg_sentences(text)) if x]
    if not regs:
        return ""
    pol = [x for x in regs if x != "반말"]
    if pol and len(pol) < len(regs):
        return "섞임"
    if not pol:
        return "반말"
    return "합쇼체" if "합쇼체" in pol else "해요체"


def register_mismatch(reply: str, target: str, allowed: list[str] | None = None) -> str:
    """답 높임이 정한 높임(target: 반말/해요체/합쇼체)과 어긋나면 이유, 맞거나 모르면 "".
    allowed: OWNER이 이 상대에게 실제로 쓰던 짧은 말(맞장구) — 그대로면 어긋남으로 보지 않는다.
    합쇼체 상대에게 해요체는 허용(한 단계 아래 존댓말), 해요체 상대에게 합쇼체도 허용."""
    r = (reply or "").strip()
    if not r or target not in ("반말", "해요체", "합쇼체"):
        return ""
    if allowed and _REG_TAIL_RE.sub("", r) in {_REG_TAIL_RE.sub("", a.strip()) for a in allowed if a}:
        return ""
    got = text_register(r)
    if not got:
        return ""
    if target == "반말" and got != "반말":
        return f"반말 상대에 {'존댓말 섞임' if got == '섞임' else '존댓말'}"
    if target != "반말" and got in ("반말", "섞임"):
        return f"{target} 상대에 {'반말 섞임' if got == '섞임' else '반말'}"
    return ""
