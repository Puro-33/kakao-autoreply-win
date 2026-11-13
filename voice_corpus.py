"""OWNER 글·말 리듬 말뭉치 (사용자 지시 2026-11-08: "Claude 채팅방 기록을 다 뒤져 봐. 내 말투가 그대로 다 녹아 있잖아…
코덱스에도 내가 넣은 프롬프트들 다 가지고 와서 내 말투 찾아보고.")

OWNER이 직접 친 문장만 뽑는다.
- Claude Code 대화 기록: ~/.claude/projects/**/*.jsonl 의 type=user, origin.kind=human 메시지(텍스트만).
  subagents/ 폴더, 봇 작업 폴더(claude-cwd), 시스템 알림·명령 출력·붙여 넣은 글·코드 블록은 뺀다.
- Codex: history.jsonl(입력한 프롬프트), thread_history_1.sqlite의 userMessage(사용자 스레드만, 서브에이전트 제외),
  sessions/**/*.jsonl(압축 안 된 것) 의 user_message. 받아쓰기(dictation-history·transcription-history.jsonl)는 source="speech".
  auth.json·secrets·.sandbox-secrets·PERSONAL_CONTEXT.md는 읽지 않는다.
원본은 읽기만 한다(sqlite도 mode=ro).

결과(ontology/voice_corpus/, 개인 학습 데이터 — .gitignore 대상):
- lines.jsonl  : {"source","date","text"(가림 처리),"msg","i","n"}
- profile.json : 출처별·전체 통계 + 규칙 + 예시
- profile.md   : 사람이 읽는 요약
voice_rules_block() 은 프롬프트에 넣을 [OWNER 글 리듬] 블록(규칙 6~10개 + 실제 예시 8~12개)을 돌려준다.
반말/존댓말은 상대별 설정(style_profile)이 정하므로 여기 규칙은 길이·끊기·말끝 리듬·군말만 다룬다.
표준 라이브러리만 사용. 카톡 발신 코드 없음.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import hashlib
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:          # py -I 로 돌려도 ontology_store·style_profile을 찾게
    sys.path.insert(0, str(BASE))
OUT_DIR = BASE / "ontology" / "voice_corpus"
HOME = Path.home()
CLAUDE_ROOT = HOME / ".claude" / "projects"
CODEX_ROOT = HOME / ".codex"
KST = dt.timezone(dt.timedelta(hours=9))

# 읽지 않는 Codex 파일·폴더(자격 증명·개인 메모)
CODEX_FORBIDDEN = ("auth.json", "secrets", ".sandbox-secrets", "PERSONAL_CONTEXT.md", ".personal-context")
# 봇이 Claude CLI를 돌리는 폴더: 거기 user 메시지는 봇이 만든 프롬프트(다른 사람 말 포함)
CLAUDE_EXCLUDE_PROJECTS = ("kakao-bot-claude-cwd",)

PASTE_CHARS = 600          # 이보다 긴 메시지는 대개 붙여 넣은 글
LINE_MAX = 300             # 한 줄이 이보다 길면 붙여 넣은 글로 본다

# ───────── 가림(bot.mask_sensitive와 같은 규칙) ─────────
_SENSITIVE_PATTERNS = [
    re.compile(r"01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}"),                 # 휴대폰 번호
    re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"),     # 일반 전화
    re.compile(r"(?<![\d-])(?=(?:\d[-\s]?){10,})(?:\d{2,6}[-\s]){2,5}\d{2,8}(?!\d)"),  # 계좌번호(묶음)
    re.compile(r"(?<!\d)\d{10,16}(?!\d)"),                              # 긴 숫자열
    re.compile(r"(?<!\d)\d{8}(?!\d)"),                                  # 8자리(학번 등)
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),       # 이메일
]


def mask_sensitive(text: str) -> str:
    out = text or ""
    for p in _SENSITIVE_PATTERNS:
        out = p.sub("[가림]", out)
    return out


# 잠금 주제(schema.json topic_lexicon)에 더해 말뭉치에서만 빼는 사적 주제
EXTRA_PRIVATE = ("금융", "은행", "주식", "코인", "투자", "연봉", "월급", "등록금", "장학금", "학자금", "카드값",
                 "전역", "군 복무", "휴학", "복학", "수술", "비밀번호", "비번", "학번", "주민번호", "여권", "통장",
                 "합격", "불합격", "재수", "삼수", "자퇴", "이별", "헤어졌", "여자친구", "여친", "남친", "엄마", "아빠",
                 "부모님", "동생", "누나", "형이", "결재", "납부", "세금", "보험")

# ───────── 메시지 걸러내기 ─────────
DROP_MSG_MARKERS = ("[SYSTEM NOTIFICATION", "<agent-message", "<task-notification", "<local-command-stdout",
                    "<local-command-caveat", "<command-name", "<scheduled-task", "<heartbeat")
DROP_MSG_PREFIX = ("[Request interrupted", "This session is being continued", "Another Claude session",
                   "[external unsupported block", "Base directory for this skill", "I hit my usage limit",
                   "[Image", "Your response above", "Caveat:", "[Usage limit", "[Earlier usage")
TAG_BLOCK = re.compile(r"<([A-Za-z][\w-]*)\b[^>]*>.*?</\1\s*>", re.S)
TAG_OPEN = re.compile(r"<([A-Za-z][\w-]*)\b[^>]*>.*\Z", re.S)          # 안 닫힌 태그는 끝까지 버림
CODE_FENCE = re.compile(r"```.*?(```|\Z)", re.S)
URL = re.compile(r"https?://\S+|www\.\S+")
KAKAO_PASTE = re.compile(r"(\[(오전|오후) \d{1,2}:\d{2}\]|^\d{4}[.-] ?\d{1,2}[.-] ?\d{1,2}.*\d{1,2}:\d{2}|님이 (들어왔|나갔)습니다"
                         r"|^\[[^\]]{1,20}\] \[|(오전|오후) \d{1,2}:\d{2},)")
HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")
CODEISH = re.compile(r"(`|[{};]|=>|::|\\\\|[A-Za-z]:\\|/[\w.-]+/|\w+\.(py|js|ts|json|md|txt|ps1|exe|html|css|jsonl|sqlite|zip)\b"
                     r"|\$\w|--\w|\w+\(\)|^\s*(def|class|import|from|cd|py|pip|git|npm)\s)")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _hid(s: str) -> str:
    """메시지 id. 숫자 없이 a~p만 써서 가림 검사(긴 숫자열)에 걸리지 않게."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12].translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))


def _date_from_iso(s) -> str:
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(KST).date().isoformat()
    except (ValueError, TypeError):
        return ""


def _date_from_epoch(x) -> str:
    try:
        x = float(x)
        if x > 1e11:
            x /= 1000.0
        return dt.datetime.fromtimestamp(x, KST).date().isoformat()
    except (ValueError, TypeError, OSError):
        return ""


class Counters(collections.Counter):
    pass


def clean_message(text: str, drop: collections.Counter, paste_limit: int | None = PASTE_CHARS) -> list[str]:
    """메시지 하나 → OWNER이 친 줄 목록(가림 전). 버린 이유는 drop에 센다."""
    t = text or ""
    if any(m in t for m in DROP_MSG_MARKERS):
        drop["msg:system/tool"] += 1
        return []
    if t.lstrip().startswith(DROP_MSG_PREFIX):
        drop["msg:system/tool"] += 1
        return []
    if "## My request for Codex:" in t:
        t = t.split("## My request for Codex:", 1)[1]
    t2 = TAG_BLOCK.sub("\n", t)
    if "<pasted_content" in t:
        drop["msg:had_paste_block(removed)"] += 1
    t2 = TAG_OPEN.sub("\n", t2)
    if "```" in t2:
        drop["msg:had_code_block(removed)"] += 1
    t2 = CODE_FENCE.sub("\n", t2)
    t2 = t2.strip()
    if not t2:
        drop["msg:empty_after_strip"] += 1
        return []
    if paste_limit and len(t2) > paste_limit:
        drop["msg:long_paste(>600)"] += 1
        return []
    raw_lines = [x.strip() for x in t2.splitlines()]
    if sum(bool(KAKAO_PASTE.search(x)) for x in raw_lines) >= 2:
        drop["msg:chat_log_paste"] += 1
        return []
    out = []
    for ln in raw_lines:
        if not ln:
            continue
        if ln.startswith((">", "|", "#", "[")) or KAKAO_PASTE.search(ln):
            drop["line:quote/table/log"] += 1
            continue
        if re.match(r"^[\"“'‘「].{15,}[\"”'’」]$", ln):
            drop["line:quoted_text"] += 1
            continue
        if CODEISH.search(ln):
            drop["line:code/path"] += 1
            continue
        ln2 = _norm(URL.sub(" ", ln))
        if not ln2 or len(re.sub(r"[\W_]", "", ln2)) == 0:
            drop["line:url_only/symbols"] += 1
            continue
        if len(ln2) > LINE_MAX:
            drop["line:too_long"] += 1
            continue
        h, lat = len(HANGUL.findall(ln2)), len(LATIN.findall(ln2))
        if h == 0 and not re.search(r"[ㄱ-ㅎㅏ-ㅣ]", ln2):
            drop["line:no_hangul(english/other)"] += 1
            continue
        if lat > h * 1.5:
            drop["line:mostly_latin"] += 1
            continue
        ln2 = re.sub(r"^(\d+[.)]|[-*•·])\s+", "", ln2)       # 목록 표시 떼기
        if ln2:
            out.append(ln2)
    if not out:
        drop["msg:no_korean_lines"] += 1
    return out


# ───────── 출처 읽기(스트리밍) ─────────
def iter_claude(root: Path, drop: collections.Counter):
    """(source, date, text) — origin.kind=human 메시지만."""
    files = sorted(p for p in Path(root).rglob("*.jsonl"))
    for p in files:
        rel = p.relative_to(root).parts
        if "subagents" in rel:
            drop["file:claude_subagent"] += 1
            continue
        if any(x in rel[0] for x in CLAUDE_EXCLUDE_PROJECTS):
            drop["file:claude_bot_cwd"] += 1
            continue
        try:
            f = open(p, encoding="utf-8", errors="replace")
        except OSError:
            drop["file:unreadable"] += 1
            continue
        with f:
            for line in f:
                if '"user"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    drop["json:bad_line"] += 1
                    continue
                if d.get("type") != "user":
                    continue
                if d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary") or d.get("toolUseResult") is not None:
                    continue
                o = d.get("origin")
                if not (isinstance(o, dict) and o.get("kind") == "human"):
                    drop["msg:claude_not_human_origin"] += 1
                    continue
                cont = (d.get("message") or {}).get("content")
                if isinstance(cont, str):
                    texts = [cont]
                elif isinstance(cont, list):
                    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in cont):
                        continue
                    texts = [b.get("text", "") for b in cont if isinstance(b, dict) and b.get("type") == "text"]
                else:
                    continue
                text = "\n".join(x for x in texts if x)
                if text.strip():
                    yield "claude", _date_from_iso(d.get("timestamp")), text


def _codex_user_threads(codex: Path, drop) -> dict[str, bool] | None:
    db = codex / "state_5.sqlite"
    if not db.exists():
        cands = sorted(glob.glob(str(codex / "state_*.sqlite")))
        if not cands:
            return None
        db = Path(cands[-1])
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        rows = con.execute("select id, source, coalesce(thread_source,'') from threads").fetchall()
        con.close()
    except sqlite3.Error:
        drop["source:codex_state_db_error"] += 1
        return None
    return {i: (not str(s).startswith("{") and ts in ("", "user")) for i, s, ts in rows}


def iter_codex(codex: Path, drop: collections.Counter):
    codex = Path(codex)
    # 1) history.jsonl: OWNER이 입력한 프롬프트
    h = codex / "history.jsonl"
    if h.exists():
        with open(h, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    drop["json:bad_line"] += 1
                    continue
                if isinstance(d.get("text"), str):
                    yield "codex", _date_from_epoch(d.get("ts")), d["text"]
    # 2) 스레드 기록(sqlite 투영). 세션 원본 대부분이 .jsonl.zst라 표준 라이브러리로 못 풀어서 이걸 쓴다
    user_threads = _codex_user_threads(codex, drop) or {}
    th = codex / "thread_history_1.sqlite"
    if not th.exists():
        cands = sorted(glob.glob(str(codex / "thread_history_*.sqlite")))
        th = Path(cands[-1]) if cands else th
    if th.exists():
        try:
            con = sqlite3.connect(f"file:{th.as_posix()}?mode=ro", uri=True)
            cur = con.execute("select thread_id, created_at_ms, item_json from thread_items "
                              "where item_type='userMessage' order by thread_id, rollout_ordinal, item_id")
            for tid, ms, j in cur:
                ok = user_threads.get(tid)
                if ok is None:
                    drop["msg:codex_unknown_thread"] += 1
                    continue
                if not ok:
                    drop["msg:codex_subagent/guardian"] += 1
                    continue
                try:
                    d = json.loads(j)
                except ValueError:
                    continue
                text = "\n".join(b.get("text", "") for b in d.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
                if text.strip():
                    yield "codex", _date_from_epoch(ms), text
            con.close()
        except sqlite3.Error:
            drop["source:codex_thread_db_error"] += 1
    # 3) 압축 안 된 세션 원본
    for p in sorted((codex / "sessions").rglob("*.jsonl")) if (codex / "sessions").exists() else []:
        meta_ok = True
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                pl = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                if d.get("type") == "session_meta":
                    src = pl.get("source")
                    meta_ok = isinstance(src, str) and pl.get("thread_source") in (None, "", "user")
                    if not meta_ok:
                        drop["file:codex_subagent_session"] += 1
                        break
                elif d.get("type") == "event_msg" and pl.get("type") == "user_message" and meta_ok:
                    if isinstance(pl.get("message"), str):
                        yield "codex", _date_from_iso(d.get("timestamp")), pl["message"]
    zst = list((codex / "sessions").rglob("*.zst")) if (codex / "sessions").exists() else []
    if zst:
        drop["file:codex_zst_not_decoded(covered_by_sqlite)"] += len(zst)


def iter_speech(codex: Path, drop: collections.Counter):
    """받아쓰기 기록. 형식이 정해지지 않아 text/transcript/content 필드나 평문 줄을 읽는다."""
    codex = Path(codex)
    files = []
    t = codex / "transcription-history.jsonl"
    if t.exists():
        files.append(t)
    dh = codex / "dictation-history"
    if dh.exists():
        files += sorted(p for p in dh.rglob("*") if p.is_file() and p.suffix.lower() in (".json", ".jsonl", ".txt"))
    for p in files:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                date, text = "", line
                if line.startswith("{"):
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    text = next((d[k] for k in ("text", "transcript", "content", "message") if isinstance(d.get(k), str)), "")
                    date = _date_from_iso(d.get("timestamp") or d.get("created_at") or "") or _date_from_epoch(d.get("ts") or "")
                if text:
                    yield "speech", date, text


# ───────── 문장·말끝·군말 분석 ─────────
FILLERS = ["근데", "그니까", "그러니까", "아니", "진짜", "좀", "그냥", "걍", "막", "약간", "일단", "이제", "혹시",
           "그래서", "그리고", "그럼", "그러면", "암튼", "아무튼", "솔직히", "사실", "뭔가", "되게", "완전", "엄청",
           "너무", "그거", "이거", "저거", "다시", "또", "제대로", "왜", "어케", "어떻게", "대충", "아예", "계속",
           "그래도", "글고", "ㄹㅇ", "제발", "혹은", "아마", "그런데", "그러면은", "그래가지고", "해가지고"]
INTERJ = ["아", "어", "오", "음", "엥", "헐", "와", "흠", "앗", "응", "ㅇㅇ", "ㅇㅋ", "ㄴㄴ", "ㄱㄱ"]
_FILLER_RE = {w: re.compile(r"(?<![가-힣])" + re.escape(w)) for w in FILLERS}
_INTERJ_RE = {w: re.compile(r"(?<![가-힣ㄱ-ㅎ])" + re.escape(w) + r"(?![가-힣ㄱ-ㅎ])") for w in INTERJ}
MARKS = {"ㅋ": r"ㅋ{2,}", "ㅎ": r"ㅎ{2,}", "ㅠ": r"[ㅠㅜ]", "~": r"~", "!": r"!", "?": r"\?", "…": r"\.\.|…",
         "이모지": r"[\U0001F300-\U0001FAFF☀-➿]"}
SPACING = [("할수", "할 수"), ("수있", "수 있"), ("수없", "수 없"), ("것같", "것 같"), ("거같", "거 같"),
           ("안돼", "안 돼"), ("안되", "안 되"), ("해줘", "해 줘"), ("해봐", "해 봐"), ("하는거", "하는 거"),
           ("한거", "한 거"), ("있는거", "있는 거"), ("이거야", "이거 야"), ("뭐야", "뭐 야")]
COLLOQ = {"어케": "어떻게", "걍": "그냥", "넘": "너무", "젤": "제일", "담에": "다음에", "글고": "그리고",
          "근데": "그런데", "그니까": "그러니까", "암튼": "아무튼", "머": "뭐", "잇": "있", "됬": "됐",
          "되요": "돼요", "할께": "할게", "몇일": "며칠", "어떻해": "어떡해", "왠만": "웬만", "금새": "금세",
          "구지": "굳이", "희안": "희한", "오랫만": "오랜만", "갈께": "갈게", "줄께": "줄게", "볼께": "볼게",
          "되서": "돼서", "안되요": "안 돼요", "됄": "될", "웬지": "왠지", "역활": "역할", "어의없": "어이없"}
_COLLOQ_RE = {w: re.compile(r"(?<![가-힣])" + re.escape(w) + (r"(?![가-힣])" if len(w) == 1 else "")) for w in COLLOQ}
SHORT_FULL = [("근데", "그런데"), ("그니까", "그러니까"), ("그럼", "그러면"), ("걍", "그냥"), ("암튼", "아무튼"),
              ("글고", "그리고"), ("어케", "어떻게"), ("젤", "제일")]
WH_RE = re.compile(r"(?<![가-힣])(왜|어디|뭐|뭘|언제|누가|누구|어떻게|어떤|몇|무슨|혹시)(?![가-힣])")
STRAY_JAMO = re.compile(r"[가-힣][ㅏ-ㅣㄱ-ㅎ](?![ㄱ-ㅎㅏ-ㅣ])(?<![ㅋㅎㅠㅜ])")   # '했어ㅓ' 같은 자모 남김

ASK_RE = re.compile(r"(\?|(까|나|냐|니|는지|을까|ㄹ까|래|지|던가|는가)[\s~.!]*$)")
AGREE_RE = re.compile(r"^(응|ㅇㅇ|ㅇㅋ|오케|오키|좋아|좋네|좋다|그래|그러자|맞아|맞네|맞지|네|넵|넹|ㄱㄱ|고고|굿|됐다|오 )")
REFUSE_RE = re.compile(r"(^아니|^ㄴㄴ|싫어|말고|하지 ?마|그만|안 ?할래|안 ?해도|필요 ?없|됐어|^노$|안 ?돼)")
PUSH_RE = re.compile(r"(^근데|^그게 아니라|^아니 |^아니,|^왜 |이상한데|이상하네|틀렸|말이 안|그건 아니|아닌데|아니잖|잖아[\s?!.~ㅋ]*$|^다시 )")


def sentences(line: str) -> list[str]:
    parts = re.split(r"(?<=[.!?~])\s+|(?<=ㅋㅋ)\s+|(?<=ㅠ)\s+|(?<=[.!?])(?=[가-힣])", line)
    return [p.strip() for p in parts if len(p.strip()) >= 2]


def ending_of(sent: str) -> tuple[str, str]:
    """(마지막 낱말, 마지막 두 글자) — 끝 문장부호·ㅋ·ㅠ 뗀 뒤 한글 기준."""
    s = re.sub(r"[\s.!?~ㅋㅎㅠㅜ;^,…)\]]+$", "", sent)
    words = s.split()
    if not words:
        return "", ""
    w = re.sub(r"[^가-힣]", "", words[-1])
    return w, w[-2:]


def _pct(n, d) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _quant(xs: list[int]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    q = lambda p: s[min(len(s) - 1, int(p * (len(s) - 1) + 0.5))]
    return {"n": len(s), "mean": round(statistics.fmean(s), 1), "p10": q(.1), "p25": q(.25), "p50": q(.5),
            "p75": q(.75), "p90": q(.9), "max": s[-1]}


def compute_stats(msgs: list[dict]) -> dict:
    """msgs: [{"lines":[...] (가림 후)}]."""
    try:
        import style_profile as SP
        qs = SP.questioner_style
    except Exception:     # noqa: BLE001 — 통계는 레지스터 없이도 낸다
        qs = None
    lines = [ln for m in msgs for ln in m["lines"]]
    nl = len(lines)
    sents = [s for ln in lines for s in sentences(ln)]
    st: dict = {"messages": len(msgs), "lines": nl, "sentences": len(sents)}
    st["msg_chars"] = _quant([sum(len(x) for x in m["lines"]) for m in msgs])
    st["line_chars"] = _quant([len(x) for x in lines])
    st["sent_chars"] = _quant([len(x) for x in sents])
    lpm = [len(m["lines"]) for m in msgs]
    st["lines_per_msg"] = _quant(lpm)
    st["multi_line_msg_pct"] = _pct(sum(n > 1 for n in lpm), len(lpm))
    st["sents_per_line_mean"] = round(len(sents) / nl, 2) if nl else 0
    # 레지스터(참고용 — 규칙에는 쓰지 않는다)
    if qs:
        reg = collections.Counter(qs("\n".join(m["lines"]))["register"] for m in msgs if m["lines"])
        st["register_msgs"] = dict(reg)
        st["polite_msg_pct"] = _pct(reg.get("존댓말", 0), sum(reg.values()))
    # 줄 끝 표시
    endp = collections.Counter()
    for ln in lines:
        e = ln.rstrip()[-1:]
        endp[{".": ".", "?": "?", "!": "!", "~": "~", "ㅋ": "ㅋ", "ㅎ": "ㅎ", "ㅠ": "ㅠ", "ㅜ": "ㅠ", ",": ","}.get(e, "없음")] += 1
    st["line_end_mark_pct"] = {k: _pct(v, nl) for k, v in endp.most_common()}
    st["mark_line_pct"] = {k: _pct(sum(bool(re.search(p, ln)) for ln in lines), nl) for k, p in MARKS.items()}
    # 말끝
    words = collections.Counter()
    tails = collections.Counter()
    for s in sents:
        w, t2 = ending_of(s)
        if w:
            words[w] += 1
        if t2:
            tails[t2] += 1
    st["top_end_words"] = words.most_common(30)
    st["top_endings"] = tails.most_common(30)
    # 군말·이음말
    st["filler_per100_lines"] = {w: round(100 * sum(bool(r.search(ln)) for ln in lines) / nl, 1) if nl else 0
                                 for w, r in _FILLER_RE.items()}
    st["filler_per100_lines"] = dict(sorted(((k, v) for k, v in st["filler_per100_lines"].items() if v > 0),
                                            key=lambda kv: (-kv[1], kv[0])))
    st["interj_per100_lines"] = dict(sorted(((w, round(100 * sum(bool(r.search(ln)) for ln in lines) / nl, 1))
                                             for w, r in _INTERJ_RE.items() if nl), key=lambda kv: (-kv[1], kv[0])))
    st["interj_per100_lines"] = {k: v for k, v in st["interj_per100_lines"].items() if v > 0}
    opener = collections.Counter()
    for ln in lines:
        fw = ln.split()[0] if ln.split() else ""
        fw = re.sub(r"[,.!?~]+$", "", fw)
        if fw in FILLERS or fw in INTERJ:
            opener[fw] += 1
    st["line_openers"] = opener.most_common(15)
    st["line_opener_filler_pct"] = _pct(sum(opener.values()), nl)
    # 띄어쓰기·구어 표기·오타
    sp = {}
    for a, b in SPACING:
        na = sum(ln.count(a) for ln in lines)
        nb = sum(ln.count(b) for ln in lines)
        if na + nb:
            sp[f"{a}/{b}"] = {"붙임": na, "띄움": nb, "붙임_pct": _pct(na, na + nb)}
    st["spacing"] = sp
    st["colloquial_spellings"] = {f"{w}(={COLLOQ[w]})": n for w, r in _COLLOQ_RE.items()
                                  if (n := sum(bool(r.search(ln)) for ln in lines))}
    st["stray_jamo_typo_lines"] = sum(bool(STRAY_JAMO.search(ln)) for ln in lines)
    pairs = {}
    for short, full in SHORT_FULL:
        ns = sum(bool(re.search(r"(?<![가-힣])" + short, ln)) for ln in lines)
        nf = sum(bool(re.search(r"(?<![가-힣])" + full, ln)) for ln in lines)
        if ns + nf:
            pairs[f"{short}/{full}"] = {"줄임": ns, "온전": nf}
    st["short_vs_full"] = pairs
    wh = [ln for ln in lines if WH_RE.search(ln)]
    st["wh_lines"] = len(wh)
    st["wh_without_qmark_pct"] = _pct(sum("?" not in ln for ln in wh), len(wh))
    st["double_qmark_lines"] = sum("??" in ln for ln in lines)
    st["no_space_after_comma_pct"] = _pct(sum(bool(re.search(r",[가-힣]", ln)) for ln in lines),
                                          sum("," in ln for ln in lines))
    # 말하기 행위
    acts = {"ask": ASK_RE, "agree": AGREE_RE, "refuse": REFUSE_RE, "pushback": PUSH_RE}
    st["acts_line_pct"] = {}
    st["acts_openers"] = {}
    st["acts_endings"] = {}
    for k, r in acts.items():
        hit = [ln for ln in lines if r.search(ln)]
        st["acts_line_pct"][k] = _pct(len(hit), nl)
        op = collections.Counter(re.sub(r"[,.!?~]+$", "", ln.split()[0]) for ln in hit if ln.split())
        st["acts_openers"][k] = [(w, n) for w, n in op.most_common(12) if n >= 2 and len(w) <= 4]
        en = collections.Counter(ending_of(ln)[0] for ln in hit)
        st["acts_endings"][k] = [(w, n) for w, n in en.most_common(12) if n >= 2 and w and len(w) <= 5]
    return st


# ───────── 예시 고르기 ─────────
# AI에게 시키는 말(카톡에서 사람에게 하는 말투가 아님) — 예시에서 뺀다
AI_CMD = re.compile(r"(해\s?줘|해\s?봐|해\s?놔|해\s?둬|줘요?|주세요|주라|줄래|해\s?줄|봐라|봐요|해라|하자|확인해|찾아|만들어|알려|"
                    r"정리해|고쳐|바꿔|추가해|넣어|빼\s?줘|돌려|써\s?줘|그려|보여|시켜|고고|가자|진행|이어서|계속해|실행해|"
                    r"설치해|검색해|조사해|읽어|올려|내려|지워|삭제해|저장해|기억해|해\s?보자|하지\s?마|말고|해야\s?해|하도록|할\s?것|"
                    r"멈췄|멈춰|멈출|끊겼|끊겨|반영해|수정해|작성해|열어|켜\s?줘|꺼\s?줘)")
AI_CMD_END = re.compile(r"(해|해요|하세요|봐|줘|라|마|둬|놔|자|고|해야지|하고|할래|꺼|켜|려|해\s?좀|좀)[\s.!?~ㅋㅎㅠㅜ]*$")
# 기술·작업 낱말(AI 작업 맥락) — 카톡 예시로 쓰면 내용이 새어 나온다
AI_CONTEXT = re.compile(r"(코드|파일|폴더|경로|설치|실행|빌드|배포|서버|테스트|커밋|깃|버전|업데이트|에러|오류|버그|로그|스크립트|"
                        r"함수|모듈|클로드|코덱스|에이전트|프롬프트|봇|모델|토큰|플러그인|스킬|앱|사이트|페이지|웹|브라우저|크롬|"
                        r"터미널|명령|설정|권한|다운|업로드|논문|과제|교수|학교|수업|시험|강의|인강|문제|정답|풀이|채팅|카톡|카카오|"
                        r"메시지|답장|대화방|대화 수집|메모리|데이터|계정|계쩡|로그인|인증|세션|구현|기능|요약|번역|출력|입력|변수|"
                        r"레포트|보고서|양식|명세|스택|플래그|초기화|할당|패딩|정렬|온톨로지|노드|조회|라이선스|액세스|프로세스|"
                        r"시뮬레이션|벤치마크|디스크|공정|최적화|소프트웨어|어시스턴트|학술|딥리서치|음성인식|수식|압축|프로젝트|"
                        r"장치|실기기|검증|연결|접속|정리했|정리 좀|내용|맥락|파악|윈도우|리눅스|칼리|지피티|인공지능|"
                        r"부싱|장력|파장|충격량|충돌량|정상파|속도에|토의|결론|제안|안내|여백|문구|실루엣|질감|배경은|"
                        r"법제처|다트|두수|버튜버|블랜더|사업자|효과음|모드|후보|확정|지게차|훈련|교육)")
YOU_AI = re.compile(r"(?<![가-힣])(너|니가|네가|너가|너는|넌|너도|당신|니|네)(?![가-힣])")
SENT_FINAL = re.compile(r"(어|아|야|지|네|데|까|나|냐|니|래|돼|군|걸|게|가|잖|음|듯|함|됨|굼뜸)[\s.!?~ㅋㅎㅠㅜ]*$")
NARRATIVE = re.compile(r"(니다|세요|시나요|나요|죠|었다|였다|했다|된다|한다|이다|있다|없다)[\s.!?~]*$")
IDENTITY = re.compile(r"(출신|이름|나이|생일|사는 곳|(?<![가-힣])[가-힣]{3}이야(?![가-힣])|(?<![가-힣])[가-힣]{1,3}대(?![가-힣]))")
ODD_JAMO = re.compile(r"[ㅏ-ㅣ]|(?<![ㄱ-ㅎ])[ㄱ-ㅊㅌㅍ](?![ㄱ-ㅎ])")   # 자모 남김·자판 오타


def exemplar_ok(text: str, store=None) -> bool:
    t = text.strip()
    if not (4 <= len(t) <= 40):
        return False
    if re.search(r"[A-Za-z0-9]|\[가림\]|[\"'“”‘’()<>/\\:=_*#@%&]", t):
        return False
    core = re.sub(r"\s", "", t)
    if len(HANGUL.findall(core)) < 0.6 * len(core):
        return False
    if AI_CMD.search(t) or AI_CMD_END.search(t) or AI_CONTEXT.search(t) or YOU_AI.search(t):
        return False
    if not SENT_FINAL.search(t) or NARRATIVE.search(t) or IDENTITY.search(t) or ODD_JAMO.search(t):
        return False
    if any(w in t for w in EXTRA_PRIVATE):
        return False
    if store is not None and (store.topic_hit(t) or store.never_hit(t)):
        return False
    return True


def pick_exemplars(lines: list[tuple[str, int]], store=None, k: int = 12) -> list[str]:
    """lines: (text, 여러 메시지에서 나온 횟수). 군말·말끝이 드러나는 짧은 줄을 행위별로 고루."""
    cands = [(t, c) for t, c in lines if exemplar_ok(t, store)]
    def score(tc):
        t, c = tc
        s = min(c, 3)
        s += sum(1 for r in _FILLER_RE.values() if r.search(t)) * 1.5
        s += 1 if re.search(r"(는데|거든|듯|같은데|잖아|ㅋㅋ|네|나|지)[\s.!?~ㅋㅠ]*$", t) else 0
        s -= 0.03 * abs(len(t) - 16)
        return s
    cands.sort(key=lambda tc: (-score(tc), tc[0]))
    buckets = [("ask", ASK_RE, 2), ("pushback", PUSH_RE, 2), ("agree", AGREE_RE, 2), ("refuse", REFUSE_RE, 1)]
    out, seen_first, seen_end = [], set(), collections.Counter()
    def take(t):
        fw = t.split()[0]
        end = ending_of(t)[1]
        if t in out or fw in seen_first or seen_end[end] >= 2:
            return False
        out.append(t)
        seen_first.add(fw)
        seen_end[end] += 1
        return True
    for _, r, n in buckets:
        got = 0
        for t, _ in cands:
            if got >= n:
                break
            if r.search(t) and take(t):
                got += 1
    for t, _ in cands:
        if len(out) >= k:
            break
        take(t)
    return out[:k]


# ───────── 규칙 만들기 ─────────
IMPERATIVE_ENDS = re.compile(r"(줘|봐|라|해|자|마|둬|놔|세요|줘요|봐요|해요|하고)$")


def _not_cmd(w: str) -> bool:
    return bool(w) and not (IMPERATIVE_ENDS.search(w) or AI_CMD.search(w) or AI_CMD_END.search(w)
                            or re.search(r"(요|니다|세요|죠)$", w))


REGISTER_WORDS = {"네", "넵", "넹", "응", "웅", "예"}


def make_rules(o: dict) -> list[str]:
    r = []
    lc, lp = o["line_chars"], o["lines_per_msg"]
    if lc.get("n"):
        r.append(f"한 줄은 보통 {lc['p50']}자 안팎(절반이 {lc['p25']}~{lc['p75']}자), {lc['p90']}자를 넘기지 않는다. "
                 f"문장 하나는 {o['sent_chars']['p50']}자 정도.")
    if lp.get("n"):
        if lp.get("p75", 1) <= 1:
            r.append(f"한 번에 한 줄로 보낸다(여러 줄 메시지 {o['multi_line_msg_pct']}%). 생각이 둘이면 "
                     "한 줄에 쉼표나 띄어쓰기로 바로 이어 붙이고, 설명 문단·목록으로 펼치지 않는다.")
        else:
            r.append(f"생각 하나에 한 줄, 보통 {lp['p50']}~{lp['p75']}줄로 끊어 보낸다(여러 줄 메시지 {o['multi_line_msg_pct']}%).")
    em = o["line_end_mark_pct"]
    r.append(f"줄 끝은 대개 아무 표시 없이 끝낸다(표시 없음 {em.get('없음', 0)}%, 마침표 {em.get('.', 0)}%, "
             f"물음표 {em.get('?', 0)}%, 느낌표 {em.get('!', 0)}%).")
    mk = o["mark_line_pct"]
    used = [f"{k} {v}%" for k, v in mk.items() if k != "?" and v >= 3]
    rare = [k for k, v in mk.items() if k != "?" and v < 3]
    r.append(("" if not used else f"{', '.join(used)}만 가끔 쓴다. ")
             + (f"{', '.join(rare)}는 OWNER 글에 거의 없다(줄의 3% 미만). 상대별 카톡 말투 통계에 있으면 그쪽을 따르고, "
                "없으면 붙이지 않는다." if rare else ""))
    fl = [w for w, v in o["filler_per100_lines"].items() if v >= 1.5][:7]
    if fl:
        op = [w for w, _ in o.get("line_openers", [])][:5]
        r.append(f"군말·이음말은 {', '.join(fl)} 정도를 섞는다. 줄 첫머리를 "
                 + (f"{', '.join(op)} 같은 말로 여는 일이 많다" if op else "이음말로 여는 일이 많다")
                 + f"(줄의 {o['line_opener_filler_pct']}%).")
    sf = o.get("short_vs_full", {})
    full_pref = [k.split("/")[1] for k, v in sf.items() if v["온전"] > v["줄임"] and v["온전"] >= 3]
    short_pref = [k.split("/")[0] for k, v in sf.items() if v["줄임"] > v["온전"] and v["줄임"] >= 3]
    if full_pref or short_pref:
        r.append("이음말 꼴: " + (f"{', '.join(full_pref)}처럼 온전하게 쓰는 편" if full_pref else "")
                 + ("이고 " if full_pref and short_pref else "") + (f"{', '.join(short_pref)}는 줄여 쓴다" if short_pref else "")
                 + ". 걍·어케·ㄹㅇ 같은 줄임말을 지어 넣지 않는다.")
    ends = [w for w, _ in o["top_end_words"] if _not_cmd(w) and 1 <= len(w) <= 4][:6]
    if ends:
        r.append("말끝은 짧은 낱말 하나로 맺는다: " + ", ".join(ends)
                 + " 같은 꼴. 반말/존댓말은 상대별 설정을 따르고, 여기선 짧게 끊는 리듬만 가져온다.")
    ask_end = [w for w, _ in o["acts_endings"].get("ask", []) if _not_cmd(w)][:4]
    r.append(f"물을 때는 바로 묻는다(질문 줄 {o['acts_line_pct']['ask']}%)"
             + (f", 말끝은 {', '.join(ask_end)}처럼 짧게" if ask_end else "")
             + f". 의문사가 있으면 물음표를 빼기도 한다(의문사 줄의 {o.get('wh_without_qmark_pct', 0)}%).")
    push_op = [w for w, _ in o["acts_openers"].get("pushback", []) if w not in REGISTER_WORDS][:4]
    ag_op = []
    for w, _ in o["acts_openers"].get("agree", []):
        w2 = re.sub(r"([ㄱ-ㅎ])\1{2,}", r"\1\1", w)        # ㄱㄱㄱ → ㄱㄱ
        if w2 not in REGISTER_WORDS and w2 not in ag_op:
            ag_op.append(w2)
    ag_op = ag_op[:3]
    r.append("반박·정정은 첫마디에서 바로 꺾는다" + (f"({', '.join(push_op)}로 시작)" if push_op else "")
             + ". 동의는 한두 낱말" + (f"({', '.join(ag_op)} 또는 짧은 대답)" if ag_op else "")
             + "로 끝낸다. 앞뒤에 사과·감사·요약을 붙이지 않는다.")
    sp = o.get("spacing", {})
    spaced = [k.split("/")[1] for k, v in sp.items() if not AI_CMD.search(k) and v["띄움"] >= 3 and v["붙임_pct"] <= 20]
    attached = [k.split("/")[0] for k, v in sp.items() if not AI_CMD.search(k) and v["붙임"] >= 3 and v["붙임_pct"] >= 50]
    if spaced or attached:
        r.append("띄어쓰기는 " + (f"대체로 맞게 쓴다({', '.join(spaced[:4])})" if spaced else "")
                 + (", 다만 " if spaced and attached else "") + (f"{', '.join(attached[:3])}처럼 붙이기도 한다" if attached else "")
                 + ". 일부러 오타를 넣거나 맞춤법을 깨지 않는다.")
    return r[:10]


# ───────── 빌드 ─────────
def _store():
    """잠금 주제 목록이 없으면 말뭉치를 만들지 않는다(사적 주제가 예시로 새는 것보다 실패가 낫다)."""
    from ontology_store import Store
    st = Store()
    if not st.ok or not st.schema.get("topic_lexicon"):
        raise RuntimeError(f"ontology_store 잠금 주제를 못 읽음: {st.problem}")
    return st


def _private_hit(store, text: str) -> str:
    if store is not None:
        if store.never_hit(text):
            return "never_store"
        if store.topic_hit(text):
            return "topic"
    elif any(p.search(text) for p in _SENSITIVE_PATTERNS):
        return "never_store"
    if any(w in text for w in EXTRA_PRIVATE):
        return "private_extra"
    return ""


SPEAKLY_DBS = [Path.home() / "Documents" / "Speakly-백업-20261108" / "genspark-flow.db",
               Path.home() / "AppData" / "Roaming" / "Speakly" / "genspark-flow.db"]


def iter_speakly(drop: collections.Counter, dbs: list[Path] | None = None):
    """Genspark Speakly 받아쓰기(history.original_text = OWNER이 실제로 말한 그대로, AI 다듬기 전).
    11/08 사용자: 'Speakly 지우기 전에 전사했던 거 다 가져와서 학습'. 백업본을 먼저 읽고, 없으면 앱 폴더."""
    import sqlite3
    for db in dbs or SPEAKLY_DBS:
        if not Path(db).exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = con.execute("SELECT original_text, created_at, status FROM history").fetchall()
            con.close()
        except sqlite3.Error:
            drop["speakly:unreadable"] += 1
            continue
        for text, created, status in rows:
            if status != "completed" or not text:
                drop["speakly:not_completed"] += 1
                continue
            yield "speech", _date_from_epoch((created or 0) / 1000 if (created or 0) > 1e11 else created), text
        return


def build(out_dir: Path | None = None, claude_root: Path | None = None, codex_root: Path | None = None,
          store=None, quiet: bool = False) -> dict:
    out_dir = Path(out_dir or OUT_DIR)
    claude_root = Path(claude_root or CLAUDE_ROOT)
    codex_root = Path(codex_root or CODEX_ROOT)
    store = store if store is not None else _store()
    drop: collections.Counter = collections.Counter()
    unavailable = []
    seen: set[str] = set()
    msgs: list[dict] = []
    raw_counts = collections.Counter()

    def gen():
        if claude_root.exists():
            yield from iter_claude(claude_root, drop)
        else:
            unavailable.append(f"claude: {claude_root} 없음")
        if codex_root.exists():
            yield from iter_codex(codex_root, drop)
            yield from iter_speech(codex_root, drop)
        else:
            unavailable.append(f"codex: {codex_root} 없음")
        yield from iter_speakly(drop)

    for source, date, text in gen():
        raw_counts[source] += 1
        key = _norm(text)
        if key in seen:
            drop["msg:duplicate"] += 1
            continue
        seen.add(key)
        lines = clean_message(text, drop, paste_limit=None if source == "speech" else PASTE_CHARS)
        if source == "speech":
            lines = [s for ln in lines for s in (sentences(ln) or [ln])]
        if not lines:
            continue
        why = next((w for ln in lines if (w := _private_hit(store, ln))), "")
        if why:
            drop[f"msg:private_{why}"] += 1
            continue
        lines = [mask_sensitive(store.mask(ln) if store is not None else ln) for ln in lines]
        msgs.append({"source": source, "date": date, "lines": lines, "id": _hid(source + "|" + key)})

    msgs.sort(key=lambda m: (m["date"], m["source"], m["id"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in msgs:
        for i, ln in enumerate(m["lines"]):
            rows.append({"source": m["source"], "date": m["date"], "text": ln, "msg": m["id"], "i": i, "n": len(m["lines"])})
    _write(out_dir / "lines.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    by_src = collections.defaultdict(list)
    for m in msgs:
        by_src[m["source"]].append(m)
    stats = {"overall": compute_stats(msgs)}
    for s in ("claude", "codex", "speech"):
        stats[s] = compute_stats(by_src.get(s, [])) if by_src.get(s) else {"messages": 0, "lines": 0}
    line_freq = collections.Counter(ln for m in msgs for ln in set(m["lines"]))
    exemplars = pick_exemplars(sorted(line_freq.items()), store, k=12)
    rules = make_rules(stats["overall"]) if stats["overall"]["lines"] else []
    if not by_src.get("speech"):
        unavailable.append("speech: Codex 받아쓰기·Speakly 기록이 비어 있음")
    unavailable.append("Clova Note·Daglo: 이 PC에 기록 없음 — 앱에서 내보내기(txt/docx) 필요")
    dates = sorted(m["date"] for m in msgs if m["date"])
    profile = {
        "version": 1,
        "note": "AI에게 쓴 프롬프트·받아쓰기에서 뽑은 OWNER 글 리듬. 반말/존댓말은 상대별 설정이 우선.",
        "date_range": [dates[0], dates[-1]] if dates else [],
        "raw_messages": dict(sorted(raw_counts.items())),
        "kept": {s: {"messages": len(by_src.get(s, [])), "lines": sum(len(m["lines"]) for m in by_src.get(s, []))}
                 for s in ("claude", "codex", "speech")},
        "dropped": dict(sorted(drop.items())),
        "unavailable": unavailable,
        "stats": stats,
        "rules": rules,
        "exemplars": exemplars,
    }
    _write(out_dir / "profile.json", json.dumps(profile, ensure_ascii=False, indent=1) + "\n")
    _write(out_dir / "profile.md", render_md(profile))
    if not quiet:
        print(f"voice_corpus: {len(msgs)} msgs, {len(rows)} lines → {out_dir}")
    return profile


def _write(p: Path, s: str) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(s, encoding="utf-8", newline="\n")
    tmp.replace(p)


def render_md(p: dict) -> str:
    o = p["stats"]["overall"]
    L = ["# OWNER 글 리듬 프로필 (voice_corpus)", "",
         "AI 채팅(Claude Code·Codex)에 OWNER이 직접 친 문장과 받아쓰기에서 뽑았다. 개인 학습 데이터 — 외부 공유 금지.",
         "반말/존댓말은 상대별 설정이 정한다. 여기서는 길이·끊기·말끝·군말 리듬만 본다.", "",
         f"- 기간: {' ~ '.join(p['date_range']) if p['date_range'] else '-'}",
         "- 남은 양: " + ", ".join(f"{s} {v['messages']}메시지/{v['lines']}줄" for s, v in p["kept"].items()),
         "- 읽은 원본 메시지: " + ", ".join(f"{k} {v}" for k, v in p["raw_messages"].items()), ""]
    L += ["## 출처별 요약", "", "| 항목 | 전체 | claude | codex | speech |", "|---|---|---|---|---|"]
    def g(s, f):
        st = p["stats"].get(s, {})
        try:
            return f(st)
        except (KeyError, TypeError):
            return "-"
    rows = [("메시지", lambda s: s["messages"]), ("줄", lambda s: s["lines"]),
            ("줄 길이 중앙값(자)", lambda s: s["line_chars"]["p50"]), ("줄 길이 p90", lambda s: s["line_chars"]["p90"]),
            ("메시지당 줄(중앙값)", lambda s: s["lines_per_msg"]["p50"]), ("여러 줄 메시지 %", lambda s: s["multi_line_msg_pct"]),
            ("존댓말 메시지 %", lambda s: s["polite_msg_pct"]), ("줄 끝 표시 없음 %", lambda s: s["line_end_mark_pct"].get("없음", 0)),
            ("ㅋㅋ 줄 %", lambda s: s["mark_line_pct"]["ㅋ"]), ("ㅠ 줄 %", lambda s: s["mark_line_pct"]["ㅠ"]),
            ("~ 줄 %", lambda s: s["mark_line_pct"]["~"]), ("! 줄 %", lambda s: s["mark_line_pct"]["!"]),
            ("? 줄 %", lambda s: s["mark_line_pct"]["?"]), ("질문 줄 %", lambda s: s["acts_line_pct"]["ask"]),
            ("반박 줄 %", lambda s: s["acts_line_pct"]["pushback"])]
    for name, f in rows:
        L.append(f"| {name} | " + " | ".join(str(g(s, f)) for s in ("overall", "claude", "codex", "speech")) + " |")
    L += ["", "## 전체 세부", "",
          f"- 줄 길이: {o.get('line_chars')}", f"- 문장 길이: {o.get('sent_chars')}", f"- 메시지 길이: {o.get('msg_chars')}",
          f"- 줄 끝 표시 비율: {o.get('line_end_mark_pct')}",
          f"- 표시가 든 줄 비율: {o.get('mark_line_pct')}",
          "- 자주 쓰는 말끝 낱말: " + ", ".join(f"{w}({n})" for w, n in o.get("top_end_words", [])[:20]),
          "- 끝 두 글자: " + ", ".join(f"{w}({n})" for w, n in o.get("top_endings", [])[:20]),
          "- 군말·이음말(100줄당): " + ", ".join(f"{w} {v}" for w, v in list(o.get("filler_per100_lines", {}).items())[:20]),
          "- 감탄·짧은 응답(100줄당): " + ", ".join(f"{w} {v}" for w, v in o.get("interj_per100_lines", {}).items()),
          "- 줄 첫 낱말(군말): " + ", ".join(f"{w}({n})" for w, n in o.get("line_openers", [])),
          f"- 띄어쓰기: {o.get('spacing')}",
          f"- 구어 표기·오타: {o.get('colloquial_spellings')} / 자모 남김 오타 줄 {o.get('stray_jamo_typo_lines')}",
          f"- 말하기 행위(줄 %): {o.get('acts_line_pct')}",
          f"- 행위별 첫마디: {o.get('acts_openers')}",
          f"- 행위별 말끝: {o.get('acts_endings')}", "",
          "## 규칙", ""] + [f"{i}. {r}" for i, r in enumerate(p["rules"], 1)]
    L += ["", "## 예시(가림·주제 걸러냄, 40자 이하)", ""] + [f"- {e}" for e in p["exemplars"]]
    L += ["", "## 버린 것", ""] + [f"- {k}: {v}" for k, v in p["dropped"].items()]
    L += ["", "## 못 읽은 출처", ""] + [f"- {u}" for u in p["unavailable"]]
    return "\n".join(L) + "\n"


def voice_rules_block(profile_path: Path | None = None) -> str:
    """프롬프트용 [OWNER 글 리듬] 블록. profile.json이 없으면 빈 문자열."""
    p = Path(profile_path or OUT_DIR / "profile.json")
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    from style_profile import strip_style_directives   # 표시·호칭·반말/존댓말·길이는 [말투 기준]만 정한다(11/09)
    rules = strip_style_directives(prof.get("rules") or [])
    try:
        store = _store()
    except Exception:     # noqa: BLE001 — 저장소가 없어도 최소 잠금 주제로 다시 거른다
        store = None
    from ontology_store import LOCKED_TOPICS_MIN
    ex = [e for e in (prof.get("exemplars") or [])
          if exemplar_ok(e, store) and not any(w in e for w in LOCKED_TOPICS_MIN)]
    if not rules:
        return ""
    out = ["[OWNER 글 리듬 — OWNER이 AI 채팅·받아쓰기에 직접 친 문장에서 뽑음. 말투는 상대별 통계가 우선이고, "
           "여기선 말끝·군말·묻고 꺾는 리듬만 따른다]"]
    out += [f"- {r}" for r in rules[:10]]
    if ex:
        out.append("OWNER이 실제로 친 짧은 줄(리듬만 참고, 내용·낱말을 그대로 가져오지 않는다):")
        out += [f"  {e}" for e in ex[:12]]
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    build()
    if "--block" in sys.argv:
        print(voice_rules_block())
