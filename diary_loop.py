"""나와의 채팅 하루 질문·일기 루프(11/09 OWNER 요청) — 문장 고르기·답 나누기·프로필 반영·되돌리기만.

발신 코드 없음: 보내기·받기·Claude 호출은 bot.py가 한다(이 모듈은 문자열과 파일만 다룬다).
- 질문 후보: (a) 봇이 '자기 사실 근거 없음'으로 보류한 주제(상대 이름·원문 없이 주제어만)
             (b) 프로필(profile.slim.md) 빈 절·자리표시 줄·'~중/예정' 같은 오래되기 쉬운 줄
             (c) OWNER이 최근 직접 쓴 카톡에서 보이는 새 활동 낱말
- 답: 'Q1 …' 꼴 줄은 그 질문의 직접 답, 나머지는 일기. 원문은 data/private/diary/YYYY-MM-DD.md 에만.
- 반영: 직접 답에서 나온 OWNER 본인 사실 → 프로필 절에 바로(백업, 중복 금지),
        일기에서만 나온 사실 → '## 메모(봇에 안 넣음)' 아래 '- 후보: …' 줄.
- 비공개(입시·성적·학점·병역·돈·가족·건강·학번·연락처·계정·키)와 다른 사람 이름이 든 문장은 넣지 않는다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

SCOPES = ("모두", "학교·교수·친구", "친구")
KST = dt.timezone(dt.timedelta(hours=9))   # bot._iso가 쓰는 한국 시간(PC 시간대와 무관)
MEMO_HEAD_PREFIX = "## 메모"
CAND_PREFIX = "- 후보: "
PUSH_GRACE_SEC = 600   # 질문·일기 요청을 큐에 넣은 뒤 카톡 전송까지 기다려 주는 시간(점검이 '보내는 중'으로 봄)

# tools/apply_profile_review.PRIVATE_RE 와 같은 것 + 더 넓게(이 루프는 OWNER이 자유롭게 쓴 일기를 받기 때문)
BASE_PRIVATE_RE = re.compile(r"입시|성적|학점|점수|병역|군대|등록금|분납|계좌|송금|대출|월세|가족|건강|병원|학번"
                             r"|\d{2,3}-\d{3,4}-\d{4}|@\w+\.\w+|sk-[A-Za-z0-9]")
EXTRA_PRIVATE_RE = re.compile(
    r"입대|전역|군\s?복무|훈련소|예비군|공익|용돈|빚|돈(?:이|을|은|도|만|으로)?(?![가-힣])|월급|알바비|장학금|생활비|카드값|카드\s?번호|통장|이체"
    r"|엄마|아빠|부모|어머니|아버지|누나|언니|동생|할머니|할아버지|친척|집안"
    r"|아파|아픈|아팠|몸살|감기|두통|우울|불안|약\s?먹|진료|수술|입원|치료|상담\s?받"
    r"|학생\s?번호|주민|전화\s?번호|휴대폰\s?번호|연락처|이메일|메일\s?주소|주소(?:는|가|지)|\d{7,}"   # 학번·계좌·번호(숫자 7자리+)
    r"|비밀\s?번호|비번|패스워드|password|아이디(?!어)|계정|로그인|토큰|token|api\s?키|API\s?키|키\s?값|secret", re.I)
# 다른 사람을 가리키는 꼴(이름 + 호칭)
OTHER_PERSON_RE = re.compile(r"[가-힣]{2,4}\s?(?:님|씨|교수님?|선배|후배|쌤|선생님?|형(?=[이은한께])|누나)")
BANMAL_BAD_END_RE = re.compile(r"(?:요|니다|세요|십니까|습니까|까요)\s*[?.!~]*$")
Q_LINE_RE = re.compile(r"^\s*[Qq]\s?(\d{1,2})\s*[.:)\]\-]?\s*(.*)$")
STALE_RE = re.compile(r"기다리는\s?중|결과\s?기다|준비\s?중|준비|진행\s?중|예정|신청|하는\s?중|중$")
PLACEHOLDER_RE = re.compile(r"^-\s*\(.*\)\s*$|\(…\)|\(\.\.\.\)|…\s*$")
ACT_NOUN_RE = re.compile(r"프로젝트|플젝|동아리|스터디|인턴|알바|대회|ctf|해커톤|수업|강의|학원|대학원|연구실|랩실|창업|외주"
                         r"|부트캠프|자격증|세미나|멘토링|공모전|발표|과제|시험|봉사|면접|채용|지원", re.I)
ACT_START_RE = re.compile(r"시작|신청|합격|붙었|들어가|들어감|하기로|지원했|참가|나가|준비|진행|맡았|하게\s?됐|등록")


def private_hit(s: str) -> bool:
    return bool(BASE_PRIVATE_RE.search(s or "") or EXTRA_PRIVATE_RE.search(s or ""))


def names_hit(s: str, names) -> bool:
    s = s or ""
    if OTHER_PERSON_RE.search(s):
        return True
    for n in names or ():
        n = str(n or "").strip()
        if len(n) >= 2 and n in s:
            return True
    return False


def norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "").lstrip("- ")).rstrip(".")


def key(src: str, text: str) -> str:
    return f"{src}:" + hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:10]


# ───────────────────────── 질문 후보 ─────────────────────────
def topic_words(text: str, stop=(), names=(), n: int = 3) -> str:
    """(a) 보류한 받은 말에서 주제어만(두 글자 이상, 흔한 말·사람 이름·비공개 뺌). 원문은 남기지 않는다."""
    toks = []
    for t in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]+|[가-힣]{2,}", text or ""):
        t2 = re.sub(r"(?:이랑|에서|으로|한테|는|은|이|가|을|를|도|랑|에|로|요|야|냐|니)$", "", t) if re.match(r"[가-힣]", t) else t
        if len(t2) < 2 or t2 in stop or t in stop or private_hit(t2) or names_hit(t2, names):
            continue
        if re.fullmatch(r"(?:혹시|그럼|근데|있어|있냐|했어|해봤|해본|어때|뭐야|뭐해|알아|아는|하고|있나|있음|봤어|써봤)\w*", t2):
            continue
        if t2 not in toks:
            toks.append(t2)
    return " ".join(toks[:n])


def profile_parse(text: str) -> list[tuple[str, str, list[str]]]:
    """[(머리줄 '## …', 범위 문자열 또는 '', 줄들)]. HTML 주석은 뺀다."""
    txt = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    out, cur = [], None
    for line in txt.splitlines():
        m = re.match(r"^##\s+(.+?)\s*(?:\[([^\]]+)\])?\s*$", line)
        if m:
            cur = (line.strip(), (m.group(2) or "").strip(), [])
            out.append(cur)
            continue
        if line.startswith("#"):
            cur = None
            continue
        if cur is not None and line.strip().startswith("-"):
            cur[2].append(line.strip())
    return out


def profile_questions(text: str) -> list[dict]:
    """(b) 빈 절·자리표시 줄 먼저, 그다음 '~중·예정·신청' 같은 오래되기 쉬운 줄."""
    holes, stale = [], []
    for head, scope, lines in profile_parse(text):
        if not scope:
            continue   # 범위 없는 절(메모)은 묻지 않는다
        title = re.sub(r"^##\s+|\s*\[[^\]]*\]\s*$", "", head).strip()
        real = [l for l in lines if not PLACEHOLDER_RE.search(l)]
        if not real:
            holes.append({"src": "hole", "topic": key("hole", head), "hint": title,
                          "fallback": f"프로필 '{title}' 칸이 비어 있어. 뭐라고 채울까?"})
        for l in lines:
            if PLACEHOLDER_RE.search(l):
                holes.append({"src": "hole", "topic": key("hole", l), "hint": f"{title}: {l[2:]}",
                              "fallback": f"프로필 '{title}'에 빈 줄이 있어. 채울 거 있어?"})
            elif STALE_RE.search(l) and not private_hit(l):
                body = l.lstrip("- ").strip()
                stale.append({"src": "stale", "topic": key("stale", l), "hint": f"{title}: {body}",
                              "fallback": f"프로필에 '{body[:40]}'라고 돼 있는데 아직 그대로야?"})
    return holes + stale


def activity_questions(owner_texts: list[str], profile_text: str, names=()) -> list[dict]:
    """(c) OWNER이 최근 직접 쓴 말에서 '시작·신청·준비…' + 활동 낱말이 같이 있고, 그 낱말이 프로필에 없는 것."""
    low = (profile_text or "").lower()
    out, seen = [], set()
    for t in owner_texts or []:
        t = " ".join(str(t or "").split())
        if not t or private_hit(t) or not ACT_START_RE.search(t):
            continue
        for m in ACT_NOUN_RE.finditer(t):
            noun = m.group(0)
            # 앞에 붙은 꾸밈말 한 덩어리까지(예: 'AI 공모전'), 사람 이름·비공개면 낱말만
            pre = re.findall(r"([A-Za-z0-9가-힣]{2,10})\s?$", t[:m.start()])
            phrase = (pre[0] + " " + noun) if pre and not names_hit(pre[0], names) and not private_hit(pre[0]) \
                and not re.fullmatch(r"[가-힣]*(?:이|가|은|는|을|를|도|에|랑)", pre[0]) else noun
            k = key("act", noun.lower())
            if k in seen or noun.lower() in low:
                continue
            seen.add(k)
            out.append({"src": "act", "topic": k, "hint": phrase,
                        "fallback": f"요즘 {phrase} 하는 거 있어? 뭔지 한 줄로 알려줘"})
    return out


# 11/11 OWNER 지적(11/09 23:50·11/10 22:44에 같은 Q1·Q2가 다시 나감, 부전공은 11/09에 이미 확인):
# 최근 CONFIRM_DAYS일 안에 OWNER이 확인한 사실은 묻지 않는다. 근거는 profile.slim.md 메모 절의 두 가지 줄
#   '- 확인 YYYY-MM-DD: 낱말, 낱말'           → 그 낱말이 든 질문 후보를 뺀다
#   '- YYYY-MM-DD … 직접 확인한 내용으로 채움'  → 그날 프로필 전체를 확인했다고 보고 '아직 맞아?'(stale) 후보를 뺀다
CONFIRM_DAYS = 7
CONFIRM_LINE_RE = re.compile(r"^-\s*확인\s+(\d{4}-\d{2}-\d{2})\s*:\s*(.+)$")
FILLED_LINE_RE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\s.*직접\s?확인한\s?내용으로\s?채움")


def _recent(day: str, today: str, days: int) -> bool:
    """day가 today 기준 최근 days일 안(오늘 포함). PC 시계가 어긋나 day가 오늘보다 뒤면 최근으로 본다."""
    try:
        delta = (dt.date.fromisoformat(today) - dt.date.fromisoformat(day)).days
    except ValueError:
        return False
    return delta < days


def recent_confirmed(profile_text: str, today: str, days: int = CONFIRM_DAYS) -> tuple[bool, list[str]]:
    """(프로필 전체를 최근 확인했는가, 최근 확인한 낱말들)."""
    whole, words = False, []
    for line in (profile_text or "").splitlines():
        line = line.strip()
        m = CONFIRM_LINE_RE.match(line)
        if m and _recent(m.group(1), today, days):
            words += [w.strip() for w in re.split(r"[,，·/]", m.group(2)) if len(w.strip()) >= 2]
            continue
        m = FILLED_LINE_RE.match(line)
        if m and _recent(m.group(1), today, days):
            whole = True
    return whole, words


def drop_confirmed(cands: list[dict], profile_text: str, today: str, days: int = CONFIRM_DAYS) -> list[dict]:
    """최근 확인한 사실을 묻는 후보를 뺀다(어제 못 물은 질문 'again'도 같은 기준)."""
    whole, words = recent_confirmed(profile_text, today, days)
    out = []
    for c in cands:
        text = " ".join(str(c.get(k) or "") for k in ("hint", "q", "fallback"))
        stale = c.get("src") == "stale" or str(c.get("topic") or "").startswith("stale:")
        if (whole and stale) or any(w in text for w in words):
            continue
        out.append(c)
    return out


def sent_today(records: list[dict]) -> tuple[set, set]:
    """오늘 이미 보낸 질문의 (주제 키, 문장). records는 state.json diary_loop.sent_today([{topic, q}]) —
    bot._dl_roll이 KST 날짜가 바뀔 때 비운다. 같은 질문은 하루 한 번만."""
    topics, texts = set(), set()
    for p in records or []:
        if isinstance(p, dict):
            topics.add(str(p.get("topic") or ""))
            texts.add(str(p.get("q") or ""))
    topics.discard("")
    texts.discard("")
    return topics, texts


def gap_question(words: str) -> dict:
    return {"src": "gap", "topic": key("gap", words), "hint": words,
            "fallback": f"누가 '{words}' 물어봤는데 프로필에 없어서 보류했어. 뭐라고 답하면 돼?"}


def clean_question(q: str, names=()) -> str:
    """Claude가 만든 질문 문장 검사: 한 줄·짧게·반말·비공개/이름 없음. 아니면 ''."""
    q = (q or "").strip().splitlines()[0].strip() if (q or "").strip() else ""
    q = re.sub(r"^\s*[Qq]\s?\d+\s*[.:)\-]?\s*", "", q).strip().strip('"“”')
    if not (4 <= len(q) <= 90) or private_hit(q) or names_hit(q, names) or BANMAL_BAD_END_RE.search(q):
        return ""
    return q


# ───────────────────────── 답 나누기·일기 파일 ─────────────────────────
def split_answer(text: str) -> list[tuple[int | None, str]]:
    """'Q1 …'로 시작하는 줄부터 다음 'Qn' 줄·빈 줄 전까지는 그 질문의 답, 나머지는 일기(None)."""
    parts: list[list] = []
    in_q = False
    for line in (text or "").splitlines():
        m = Q_LINE_RE.match(line)
        if m:
            parts.append([int(m.group(1)), m.group(2).strip()])
            in_q = True
        elif not line.strip():
            in_q = False
        elif in_q:
            parts[-1][1] = (parts[-1][1] + "\n" + line.strip()).strip()
        else:
            parts.append([None, line.strip()])
    merged: list[tuple[int | None, str]] = []
    for n, body in parts:
        if n is None and merged and merged[-1][0] is None:
            merged[-1] = (None, (merged[-1][1] + "\n" + body).strip())
        else:
            merged.append((n, body.strip()))
    return [(n, b) for n, b in merged if b]


def diary_append(diary_dir: Path, when: dt.datetime, title: str, body: str) -> Path:
    diary_dir.mkdir(parents=True, exist_ok=True)
    p = diary_dir / f"{when:%Y-%m-%d}.md"
    head = "" if p.exists() else f"# 일기 {when:%Y-%m-%d} (원문, 비공개 — data/private 밖으로 내보내지 않음)\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{head}\n## {when:%H:%M} {title}\n\n{body.strip()}\n")
    return p


# ───────────────────────── 사실 뽑기(Claude 출력 검사) ─────────────────────────
def extract_prompt(sections: list[str]) -> str:
    return (
        "너는 OWNER 본인의 메모를 정리한다. 아래 [직접 답](OWNER이 질문에 답한 것)과 [일기]에서, 다른 사람이 OWNER에게 "
        "카톡으로 물었을 때 답에 쓸 수 있는 OWNER 본인에 대한 사실만 뽑는다.\n"
        "- OWNER 본인 사실만(하는 일·학교·수업·만든 것·쓰는 도구·관심·계획·경험). 그날 기분·감상·잡담은 뽑지 않는다.\n"
        "- 다른 사람 이름이나 다른 사람에 대한 사실은 쓰지 않는다.\n"
        "- 입시·성적·학점·병역·돈·등록금·계좌·가족 사정·건강·학번·연락처·계정·키는 절대 뽑지 않는다.\n"
        "- fact는 프로필 줄처럼 짧은 명사형/평서형 한 줄(마침표 없이, 40자 안팎).\n"
        f"- section은 가능하면 기존 절 이름 중 하나: {', '.join(sections)}. 맞는 게 없으면 짧은 새 이름.\n"
        f"- scope는 {', '.join(SCOPES)} 중 하나(애매하면 친구).\n"
        "- from은 사실이 나온 곳의 표지(A1, D1 …) 그대로.\n"
        "- 최대 5개. 없으면 빈 목록.\n"
        '출력은 JSON 하나만: {"facts":[{"from":"A1","section":"하는 일","scope":"모두","fact":"…"}]}'
    )


def parse_facts(out: str) -> list[dict]:
    if not out:
        return []
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return []
    facts = d.get("facts") if isinstance(d, dict) else None
    return [f for f in facts if isinstance(f, dict)] if isinstance(facts, list) else []


def vet_fact(f: dict, names=()) -> tuple[dict | None, str]:
    """(정리된 사실 또는 None, 버린 이유)."""
    fact = " ".join(str(f.get("fact") or "").split()).lstrip("- ").rstrip(".").strip()
    section = " ".join(re.sub(r"[\[\]#\n]", "", str(f.get("section") or "")).split())[:15] or "하는 일"
    if section.startswith("메모"):   # '## 메모(봇에 안 넣음)'과 헷갈리는 절 이름은 안 쓴다(후보 줄이 봇이 읽는 절로 새지 않게)
        section = "기타"
    scope = str(f.get("scope") or "").strip()
    scope = scope if scope in SCOPES else "친구"
    if not (3 <= len(fact) <= 90):
        return None, "길이"
    if private_hit(fact) or private_hit(section):
        return None, "비공개"
    if names_hit(fact, names) or names_hit(section, names):   # 절 이름(새 '## …' 머리줄)에도 다른 사람 이름 금지
        return None, "다른 사람"
    return {"from": str(f.get("from") or ""), "section": section, "scope": scope, "fact": fact}, ""


# ───────────────────────── 프로필 파일 고치기 ─────────────────────────
def _backup(profile: Path) -> Path:
    bak = profile.with_name(f"{profile.name}.bak-{dt.datetime.now():%Y%m%d%H%M%S%f}")
    shutil.copy2(profile, bak)
    return bak


def _memo_head(lines: list[str]) -> str | None:
    """봇이 안 읽는 메모 절 머리줄('## 메모…', 범위 [..] 없음). '## 메모 [친구]'처럼 범위가 붙은 절은 봇이 읽으므로 아님."""
    return next((l for l in lines if l.startswith(MEMO_HEAD_PREFIX) and "[" not in l), None)


def profile_add(profile: Path, items: list[dict], candidate: bool, when: str = "") -> list[dict]:
    """items: [{'section','scope','fact'}]. 넣은 줄 목록 [{'head','line'}](중복은 건너뜀). 고치기 전 백업.
    후보 줄('- 후보: …')에만 있는 사실은 직접 답으로 다시 오면 프로필 절에 넣는다(후보 → 반영)."""
    try:
        text = profile.read_text(encoding="utf-8")
    except OSError:
        text = "# OWNER\n\n## 메모(봇에 안 넣음)\n"
    lines = text.splitlines()
    have_live = {norm(l) for l in lines if l.strip().startswith("-") and not l.startswith(CAND_PREFIX)}
    have_cand = {norm(l[len(CAND_PREFIX):].split(" (")[0]) for l in lines if l.startswith(CAND_PREFIX)}
    added = []
    for it in items:
        fact = it["fact"]
        k = norm(fact)
        if k in have_live or (candidate and k in have_cand):
            continue
        if candidate:
            head = _memo_head(lines)
            if head is None:
                lines += ["", "## 메모(봇에 안 넣음)"]
                head = lines[-1]
            line = f"{CAND_PREFIX}{fact} ({it['section']} [{it['scope']}]{', ' + when if when else ''} 일기)"
            i = lines.index(head) + 1
            while i < len(lines) and lines[i].startswith("- "):
                i += 1
            lines.insert(i, line)
        else:
            head = f"## {it['section']} [{it['scope']}]"
            line = f"- {fact}"
            if head in lines:
                i = lines.index(head) + 1
                while i < len(lines) and lines[i].startswith("- "):
                    i += 1
                lines.insert(i, line)
            else:
                mh = _memo_head(lines)
                memo = lines.index(mh) if mh is not None else len(lines)
                lines[memo:memo] = [head, line, ""]
        (have_cand if candidate else have_live).add(k)
        added.append({"head": head, "line": line, "fact": fact, "candidate": candidate, "removed": False})
    if added:
        if profile.exists():
            _backup(profile)
        tmp = profile.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(profile)
    return added


def profile_remove(profile: Path, entry: dict) -> bool:
    """'!빼 N': 방금 넣은 줄 하나를 지운다(그 절 아래 첫 일치, 없으면 파일 전체에서 첫 일치). 백업 남김."""
    try:
        lines = profile.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    target = entry.get("line", "")
    idx = None
    if entry.get("head") in lines:
        i = lines.index(entry["head"]) + 1
        while i < len(lines) and lines[i].startswith("- "):
            if lines[i] == target:
                idx = i
                break
            i += 1
    if idx is None and target in lines:
        idx = lines.index(target)
    if idx is None:
        return False
    _backup(profile)
    del lines[idx]
    tmp = profile.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(profile)
    return True


def feedback_text(added: list[dict], dropped: int, saved: bool = True, claude_ok: bool = True,
                  private: int = 0, failed: list | tuple = ()) -> str:
    """dropped: 검사에서 버린 사실 수(비공개 포함), private: 그중 비공개(또는 답 자체가 비공개라 안 뽑힌 것) 수,
    failed: 넣었다고 나왔지만 다시 읽은 파일에 없는 줄(쓰기 실패 — 넣었다고 말하지 않는다)."""
    prof = [a for a in added if not a["candidate"]]
    cand = [a for a in added if a["candidate"]]
    n = 0
    p_txt, c_txt = [], []
    for a in prof:
        n += 1
        p_txt.append(f"{n}) {a['fact']}")
    for a in cand:
        n += 1
        c_txt.append(f"{n}) {a['fact']}")
    head = "받았어 — " + ("일기에 저장함. " if saved else "")
    if not claude_ok:
        return head + "프로필 반영은 못 했어(Claude 응답 없음). '!일기반영'으로 다시 해 봐"
    other = max(0, int(dropped) - min(int(private), int(dropped)))
    why = ([f"비공개라 안 넣음 {private}"] if private else []) + ([f"다른 사람 얘기 등 {other}"] if other else [])
    tail = f" / 뺀 것 {private + other}개({', '.join(why)})" if why else ""
    fail = (" / ⚠️ 프로필 쓰기 확인 실패라 안 들어감: " + ", ".join(str(f) for f in failed)) if failed else ""
    if not added:
        return head + "프로필에 넣을 새 사실은 없었어" + tail + fail
    s = head + "프로필에 넣은 것: " + (" ".join(p_txt) if p_txt else "없음")
    s += " / 후보로만 둔 것: " + (" ".join(c_txt) if c_txt else "없음")
    s += tail + fail
    s += f" / 빼려면 '!빼 {1 if n == 1 else '번호'}'"
    return s


FB_ITEM_RE = re.compile(r"(\d+)\) (.+?)(?= \d+\) |$)")


def feedback_claims(fb: str) -> tuple[list[str], list[str]]:
    """피드백 문장에서 '프로필에 넣은 것'·'후보로만 둔 것' 줄을 그대로 뽑는다."""
    def seg(label: str) -> list[str]:
        m = re.search(label + r": (.*?)(?: / |$)", fb or "")
        if not m or m.group(1).strip() == "없음":
            return []
        return [x[1].strip() for x in FB_ITEM_RE.findall(m.group(1).strip())]
    return seg("프로필에 넣은 것"), seg("후보로만 둔 것")


def verify_feedback(fb: str, before: str, after: str) -> tuple[bool, str]:
    """'그럴듯한 답만 하고 반영 0'을 잡는다: 피드백이 넣었다고 한 줄이 실제 파일에 새로 생겼는지.
    (ok, 설명). 넣었다는 줄이 하나도 없으면 ok=None 대신 (True, '주장 없음')."""
    put, cand = feedback_claims(fb)
    b, a = set((before or "").splitlines()), set((after or "").splitlines())
    bad = [f for f in put if f"- {f}" not in a or f"- {f}" in b]
    a_c = [l for l in a - b if l.startswith(CAND_PREFIX)]
    bad += [f for f in cand if not any(l.startswith(f"{CAND_PREFIX}{f} (") for l in a_c)]
    if bad:
        return False, "피드백엔 넣었다는데 파일에 새로 안 생긴 줄: " + ", ".join(bad)
    if not put and not cand:
        return True, "넣었다는 줄 없음"
    return True, f"피드백의 {len(put) + len(cand)}줄이 파일에 새로 있음"


# ───────────────────────── 루프 점검('!루프점검'·tools/check_diary_loop.py) ─────────────────────────
def _ts(s) -> float:
    try:
        return dt.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _hm(ts) -> str:
    try:
        return dt.datetime.fromtimestamp(float(ts), KST).strftime("%m/%d %H:%M") if ts else "-"
    except (TypeError, ValueError, OSError, OverflowError):
        return "-"


DELIV_HOW_KO = {"echo": "내 계정 글로 되돌아옴", "db": "카톡 DB에 같은 글", "db_mine": "카톡 DB에 내 계정 글",
                "room": "카톡 방 갱신 시각", "late": "늦게 들어감(지연 전달)"}


def push_delivery(d: dict, delivery: dict | None, now: float | None = None) -> tuple[bool | None, str]:
    """마지막 질문·일기 요청(push)이 카톡에 '실제로' 들어갔는가(11/09). Iris 성공(last_push_sent_at)이 아니라 봇의
    전달 확인 기록(state.json delivery.recs — 내 계정 에코·카톡 DB chat_logs 대조, last_push_rec로 찾음)으로 본다.
    '!루프점검'·bot.diary_health('undelivered')·tools/check_diary_loop.py 공용(같은 판단).
    True=카톡에 들어감 / False=미전달(카톡 DB에 없음, 또는 보냈다는 기록만 있고 Iris 전송조차 없음) /
    None=아직 판단 못 함(보내는 중·확인 대기·확인 불가(DB 조회 실패)·전달 기록 없음)."""
    now = time.time() if now is None else float(now)
    q_at = max(_ts(d.get("last_q_sent")), _ts(d.get("last_diary_req")))
    if not q_at:
        return None, "보낸 질문·일기 요청 없음"
    if "last_push_sent_at" in d and _ts(d.get("last_push_sent_at")) < q_at:   # 키가 없으면(예전 기록) 이 판단은 건너뜀
        if now - q_at < PUSH_GRACE_SEC:
            return None, "보내는 중(큐)"
        return False, f"보냈다는 기록({_hm(q_at)})만 있고 Iris 전송도 없음 — 보내기 막힘으로 보류됐거나 전송 실패"
    rid = d.get("last_push_rec")
    recs = (delivery or {}).get("recs") if isinstance(delivery, dict) else None
    rec = next((r for r in recs or [] if isinstance(r, dict) and rid is not None and r.get("id") == rid), None)
    if rec is None:
        return None, "전달 확인 기록 없음(전달 확인 기능 전 전송이거나 오래돼 지워짐)"
    st, sent = rec.get("st"), _hm(rec.get("at"))
    if st == "ok":
        return True, f"카톡에 들어감({DELIV_HOW_KO.get(rec.get('how'), rec.get('how') or '확인')}, 보낸 {sent})"
    if st == "miss":
        return False, (f"카톡 DB에 없음(보낸 {sent}, 확인 {_hm(rec.get('miss_at'))}) — "
                       "에뮬레이터 카톡 화면의 '!'·보안인증·로그인 확인")
    if st == "unknown":
        return None, f"확인 불가(Iris DB 조회 실패, 보낸 {sent}) — 미전달로 단정 안 함, 다음 점검에서 다시"
    return None, f"확인 대기(보낸 {sent}, 에코·카톡 DB로 확인 전)"


def loop_check(d: dict, profile_text: str, diary_dir: Path, enabled: bool = True,
               now: float | None = None, delivery: dict | None = None) -> list[tuple[str, bool | None, str]]:
    """질문→답→저장→반영→피드백 단계별 (이름, ok(True/False/None=아직 해당 없음), 설명).
    '그럴듯하게 답만 하고 루프가 안 도는' 경우(답 받았는데 저장·반영·피드백이 없음)를 FAIL로 드러낸다.
    질문·일기 요청도 '보냈다고 기록'만이 아니라 카톡 전송 기록(last_push_sent_at)까지 보고,
    '실제 전달(DB)'로 카톡에 실제로 들어갔는지(delivery: state.json delivery, push_delivery)까지 본다(11/09)."""
    now = time.time() if now is None else float(now)
    rows: list[tuple[str, bool | None, str]] = []
    rows.append(("예약", True if enabled else None, "켜짐" if enabled else "꺼짐(owner_diary_loop=false, !질문·!일기는 됨)"))
    q_at = max(_ts(d.get("last_q_sent")), _ts(d.get("last_diary_req")))
    a_at, f_at = _ts(d.get("last_answer_at")), _ts(d.get("last_feedback_at"))
    p_at = _ts(d.get("last_push_sent_at"))
    if not q_at:
        q_ok = None
    elif p_at >= q_at or "last_push_sent_at" not in d:
        q_ok = True
    else:
        q_ok = None if now - q_at < PUSH_GRACE_SEC else False   # 큐에서 보내는 중이면 아직 판단 안 함
    rows.append(("질문·일기 요청 보냄", q_ok,
                 f"마지막 {d.get('last_q_sent') or '-'} / 일기 요청 {d.get('last_diary_req') or '-'}"
                 f" / 카톡 전송 {d.get('last_push_sent_at') or '-'}"))
    rows.append(("실제 전달(DB)", *push_delivery(d, delivery, now)))
    pend = [p for p in d.get("pending_qs") or [] if not p.get("answered")]
    rows.append(("답 받음", (a_at >= q_at) if q_at and a_at else None,
                 f"마지막 답 {d.get('last_answer_at') or '-'}, 답 기다리는 질문 {', '.join('Q%s' % p.get('n') for p in pend) or '없음'}"))
    if a_at:
        day = dt.datetime.fromtimestamp(a_at, KST).strftime("%Y-%m-%d")
        f = Path(diary_dir) / f"{day}.md"
        rows.append(("원문 저장", f.exists(), f"{f.name} {'있음' if f.exists() else '없음'}"))
        sent = d.get("last_feedback_sent_at")
        s_at = _ts(sent)
        fb_ok = f_at >= a_at and (s_at >= a_at if "last_feedback_sent_at" in d else True)
        rows.append(("피드백 보냄", fb_ok, f"마지막 피드백 {d.get('last_feedback_at') or '-'}, 카톡 전송 {sent or '-'}"))
        live = [a for a in d.get("last_added") or [] if not a.get("removed")]
        lines = set((profile_text or "").splitlines())
        missing = [a.get("fact") for a in live if a.get("line") not in lines]
        wf = int(d.get("last_write_failed") or 0)
        rows.append(("프로필 반영", False if wf else ((not missing) if live else None),
                     f"넣은 줄 {d.get('last_applied_n', 0)}개, 후보 {sum(1 for a in live if a.get('candidate'))}개"
                     + (f", 파일에 없는 줄 {len(missing)}개" if missing else "")
                     + (f", 쓰기 확인 실패 {wf}줄" if wf else "")))
    return rows
