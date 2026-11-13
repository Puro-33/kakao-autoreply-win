"""온톨로지 v1: 상대 카드·코드 관문·출력 검사 (ontology/DESIGN.md §5·§6·§8).

순수 함수만 둔다(카톡 발신·Iris 호출 없음). bot.py가 갠톡 답을 만들 때 부른다.
- pre_gate(text)        : LLM 호출 전 코드 판정. 약속·돈·비공개 주제·학교 질문이면 보류 이유를 돌려준다
- gate(text)            : 위와 같되 단계(hard/decide/avail)까지. decide·avail은 아래 두 검사를 통과한 답만 보낸다
- restate_problem(...)  : 'OWNER이 이미 한 말 되풀이' 답의 인용이 실제 OWNER 줄에 있는지·새 숫자/시각이 없는지
- soft_reply_problem(..): 확답 없는 완곡한 답('잠만 확인해볼게')에 수락어·숫자·시각이 없는지
- render_card(...)      : 이 상대에게 말해도 되는 사실만 골라 만든 카드(≤900자)
- output_check(...)     : 보낼 답에 비공개 어휘·학교 값·다른 상대 이름이 있으면 보류 이유
"""
from __future__ import annotations

import datetime as dt
import re

from ontology_store import Store

# 약속·출결·돈·결정 — 세 단계로 나눈다(사용자 결정 11/08: 가벼운 예/아니오는 풀고 진짜 약속은 잡아 둔다)
# HARD: LLM 판단과 무관하게 보류(돈·제출/마감·새 시각 정하기)
HARD_COMMIT_RE = re.compile(
    r"몇\s?시"
    r"|입금|송금|회비|정산|계좌|돈\s|돈을|돈이|빌려|갚|비용|얼마"
    r"|마감|까지\s?(?:해|보내|제출)|제출")
# DECIDE: LLM에 넘기되, OWNER이 [대화 기록]에서 이미 한 말을 되풀이하는 답(restates_owner, 코드가 인용 확인)만 허용
DECIDE_COMMIT_RE = re.compile(r"약속|확정|수락|결정|지원할|지원해|가입할|가입해|신청할|신청해")
# AVAIL: 가벼운 시간·가능 여부. 위 되풀이 답 또는 확답 없는 완곡한 답(soft_hold, 코드가 검사)만 허용
AVAIL_COMMIT_RE = re.compile(
    r"참석|출석|불참|결석|만나(?:자|요|요\?|ㄹ|실|볼)|시간\s?(?:돼|되|괜찮|있)|언제\s?(?:돼|되|가능|볼|만나|시간)"
    r"|가능(?:하세요|하신가요|하실까요|할까요|해\?|하냐|한가요)|올\s?수\s?있|갈\s?수\s?있|와\s?줄\s?수|해\s?줄\s?수|보내\s?줄\s?수"
    # B1(11/09 레드팀 F2): 관문을 그냥 통과하던 확답 요구
    r"|참여|올\s?거지|와야\s?(?:돼|해|함)|맞다고|내\s?줄\s?수|도와\s?줄\s?수|맡아\s?줄|가\s?줄\s?수|들어\s?올\s?수")
# '맞지?·볼까·가능?'은 흔한 말이라 시각·날짜와 같이 올 때만 avail(B1: '그럼 내일 저녁 7시 맞지?', '7시에 볼까 아니면 8시?')
TIMED_ASK_RE = re.compile(r"맞지|맞죠|볼까|봐요\?|가능\?|가능해|되지\?|되죠|어때\?")
_DAY_OR_CLOCK_RE = re.compile(r"\d{1,2}\s?(?:시|:\d{2})|오늘|내일|모레|주말|이번\s?주|다음\s?주|담주|[월화수목금토일]요일|저녁|점심|아침|밤")
# 예전 한 덩어리 정규식(다른 곳에서 '약속성 말인지' 볼 때 쓰도록 남김)
COMMIT_RE = re.compile("|".join(p.pattern for p in (HARD_COMMIT_RE, DECIDE_COMMIT_RE, AVAIL_COMMIT_RE)) + "|결제|환불")
# B15(11/09 round2): '결제·환불'은 앱 결제 오류·제품 요금 같은 일반 언급이 많다(오픈채팅 #13 '유료결제… 어떤 AI가 나을까요'가 막힘).
# OWNER 돈을 가리키는 말(결제 부탁·대신·했어?·송금·계좌·더치…)과 같이 올 때만 hard. 송금·입금·계좌·정산 등은 HARD_COMMIT_RE에 그대로
PAY_WORD_RE = re.compile(r"결제|환불")
PAY_OWNER_RE = re.compile(
    r"결제\s?(?:해\s?줘|해\s?줄|해\s?주|해\s?달|좀|부탁|대신|했(?:어|냐|니|지|죠|어요|나요)|하셨|해야\s?(?:돼|해|함|하는)|할\s?(?:게|께|래|수|사람)|했으면|해\s?놔|해\s?둬|ㄱ)"
    r"|환불\s?(?:해\s?줘|해\s?줄|해\s?주|해\s?달|좀|부탁|받았|했(?:어|냐|니|지)|해야)"
    r"|대신\s?결제|결제\s?(?:금액|대금|요청|링크)|n빵|엔빵|더치|나눠\s?(?:내|낼|결제)|반반|이체|카드\s?(?:번호|빌려|줘)"
    r"|(?<![가-힣])(?:너|니가|네가|님이|OWNER)\S{0,4}\s?(?:결제|환불)")
# 학교·학적 질문 — 학교 정체가 해소되기 전에는 보류(조건 6)
SCHOOL_Q_RE = re.compile(
    r"(?:학교|대학|캠퍼스)\s?(?:어디|어느|뭐|다녀|다니|이름)|어느\s?(?:학교|대학)|무슨\s?(?:학교|대학|과)|(?:과|학과|전공)\s?(?:어디|뭐|머|이)"
    r"|몇\s?학년|학년이|학번|휴학|재학\s?중|졸업\s?(?:언제|했|해|예정)|복전|복수\s?전공|부전공|입시")
SCHOOL_PRIVATE_RE = re.compile(r"학번|휴학|입시")
# B2: 의견·평가 질문('요즘 ○○ 어떰?') — 미루는 말('확인해 보고 알려줄게')이 어색하고 티가 남 → 보류 문구 없이 할 일만
OPINION_Q_RE = re.compile(r"어때|어떰|어떻|어땠|어떤\s?(?:거|게|편)|어케|분위기|추천|뭐가\s?(?:나|좋|낫)|낫(?:냐|나|지)|별로|생각\s?(?:해|함|하냐|있)|의견|느낌")
# round5(공개 방 답, round4 오픈채팅 #20 '어떤 스펙이나 역량을 중요하게 볼것 같나요? … 다른분들 생각이 궁금하네요'):
# 단톡·오픈채팅에서 OWNER 의견·취향·평가를 묻는 말. OPINION_Q_RE에 '~ㄹ 것 같나요?'·'생각이 궁금'·'좋아하세요?'·'어느 쪽' 등을 더한다.
# '어떻게 해?'·'어떻게 설치해요?' 같은 방법 질문은 의견이 아니다(public_opinion_q가 뺀다)
PUBLIC_OPINION_EXTRA_RE = re.compile(
    r"(?:볼|할|될|일|갈|쓸|뽑을|살|고를)\s?(?:것|거)\s?같(?:나요|아요\s?\?|으세요|으신가요|냐|니|아\s?\?|음\s?\?|습니까)"
    r"|생각(?:이|은|들|하시는\s?거)?\s?(?:궁금|어떠|어떻|어때)|어떻게\s?(?:생각|보(?:세요|시|여|나|니|냐|아|십|심)|느끼)"
    r"|중요하게\s?(?:보|생각|여기)|좋아하(?:세요|시나|시는|는\s?거|냐|니|나요|셔)|선호|최애|취향|어느\s?(?:쪽|게|거|편)|뭐가\s?더"
    r"|입장(?:이|은)?\s?(?:어떠|어떻|궁금|뭐)|견해|평가(?:해|하|는)|찬성|반대(?:해|하|예요|인가)|맞다고\s?(?:보|생각)")
_HOWTO_AFTER_RE = re.compile(r"게\s?(?:하|해|돼|되|써|쓰|만들|올리|올려|설치|깔|가|와|오|받|켜|끄|들어|연결|세팅|셋팅|적용|바꾸|바꿔|넣|빼)")


def public_opinion_q(text: str) -> str:
    """단톡·오픈채팅에서 OWNER 의견·취향·평가를 묻는 말이면 걸린 말, 아니면 ""(round5 공개 방 답).
    '어떻게 해?'처럼 '어떻' 뒤가 방법(하다·설치·올리다…)이면 의견으로 보지 않는다."""
    t = text or ""
    m = PUBLIC_OPINION_EXTRA_RE.search(t)
    if m:
        return m.group(0)
    for m in OPINION_Q_RE.finditer(t):
        if m.group(0) == "어떻" and _HOWTO_AFTER_RE.match(t, m.end()):
            continue
        return m.group(0)
    return ""


# round5(공개 방 답, round4 오픈채팅 #4 '○○형님은 그 봇 사용하시나요?' → '그건 제가 잘 모르겠네요~'):
# 다른 사람을 부르거나(첫머리 호칭) 그 사람을 주제로 높임 물음을 한 말 = 남에게 한 질문
_OTHER_TITLE = r"(?:형님|누님|선배님|선생님|변호사님|교수님|대표님|팀장님|님|씨|형|누나|언니|오빠|쌤|선배)"
_OTHER_VOC_RE = re.compile(r"^\s*(?:[^\s:]{1,8}\s?질문\s?[:：]\s*)?([가-힣A-Za-z]{2,8}?)" + _OTHER_TITLE + r"(?=[\s,!~.]|$)(?![\s,]*(?:말|얘기|글|처럼|이랑|한테|께|의))")
_OTHER_TOPIC_RE = re.compile(r"(?<![가-힣])([가-힣A-Za-z]{2,8}?)" + _OTHER_TITLE + r"(?:은|는|께서는?|도)\s")
_HON_Q_RE = re.compile(r"(?:시나요|세요\s?\?|셨나요|신가요|셨어요\s?\?|시는지|시죠\s?\?|십니까|셨습니까|시는\s?편|가져가시|쓰시|하시)")
_GENERIC_ADDR = {"여러", "다들", "다른", "모든", "모두", "그", "저", "이", "우리", "회원", "방장", "운영자", "선생", "교수", "대표", "변호사"}


def addressed_other(text: str, owner_names: tuple | list = ()) -> str:
    """단톡·오픈채팅 말이 OWNER 아닌 다른 사람에게 한 질문이면 그 사람 호칭, 아니면 "".
    (1) 첫머리에서 '○○님'·'○○형님'을 부르고 묻는 말 (2) '○○님은 …하시나요?'처럼 남을 주제로 높임 물음.
    OWNER 이름·닉네임(owner_names)이 들어 있으면 OWNER에게 한 말일 수 있어 "" — 남에게 한 말로 넘기는 건 확실할 때만."""
    t = (text or "").strip()
    if not t or not re.search(r"\?|？|나요|까요|세요|신가|십니까|냐|니\s*$", t):
        return ""
    names = [str(n).strip() for n in (owner_names or ()) if str(n or "").strip()]
    # 멘션 머리말('@닉네임 ')은 Iris가 붙이는 것이라 이름 검사에서 뺀다(r5 문제 1: 멘션이면 늘 ""이 되던 것)
    for n in sorted(names, key=len, reverse=True):
        t = re.sub(r"^\s*@\s?" + re.escape(n) + r"\s*", "", t)
    if any(n in t for n in names):
        return ""
    for rx, need_hon in ((_OTHER_VOC_RE, False), (_OTHER_TOPIC_RE, True)):
        m = rx.search(t)
        if not m:
            continue
        name = m.group(1)
        if name in _GENERIC_ADDR or any(name.startswith(n) or n.startswith(name) for n in names):
            continue
        if need_hon and not _HON_Q_RE.search(t[m.end():]):
            continue
        return m.group(0).strip()
    return ""


SCHOOL_TOKENS = ("한빛", "누리대", "hanbit", "nuritech")  # 내 학교 이름·약칭(외부 상대에게 숨길 낱말)으로 바꾸세요
GENERIC_PRIVATE = "개인 사정·진로·학업 평가·돈·다른 사람 이야기는 말하지 않고 '그건 나중에 따로 얘기할게요'처럼 1인칭으로 넘긴다."


SCHOOL_SHARE_CATEGORIES = ("학교", "교수", "친구")   # 학교 이름·수업을 말해도 되는 범주(사용자 결정 11/08: 친구 포함)


def school_value(st: Store) -> str:
    for f in st.data["facts"].values():
        if f["subject"] == "me" and f["predicate"] == "school" and f["status"] == "active":
            return f["value"]
    return ""


def school_unresolved(st: Store) -> bool:
    facts = [f for f in st.data["facts"].values() if f["subject"] == "me" and f["predicate"] == "school"]
    return not any(f["status"] == "active" for f in facts)


def gate(text: str, st: Store, category: str | None = None, pay_soft: bool = True) -> tuple[str, str]:
    """코드 관문 단계와 이유. 단계: ""(통과) / "hard"(바로 보류) / "decide"(되풀이 답만) / "avail"(되풀이·완곡 답만).
    여러 개 걸리면 가장 엄한 단계.
    pay_soft(B15, 기본 켬): '결제·환불'은 OWNER 돈을 가리키는 말(PAY_OWNER_RE)과 같이 올 때만 hard. 끄면 예전처럼 늘 hard."""
    t = text or ""
    hit = st.topic_hit(t)
    if hit and hit in ("결제", "환불") and pay_soft and not PAY_OWNER_RE.search(t):
        hit = st.topic_hit(PAY_WORD_RE.sub(" ", t))   # B15: 일반 결제 언급만이면 다른 비공개 낱말이 있는지 다시 본다
    if hit:
        return "hard", "비공개 주제"
    if st.never_hit(t):
        return "hard", "개인정보 포함"
    if SCHOOL_Q_RE.search(t) and (school_unresolved(st) or category not in SCHOOL_SHARE_CATEGORIES):
        return "hard", "학교·학적 질문"
    if SCHOOL_PRIVATE_RE.search(t):   # B2(레드팀 F3): 학번·휴학·입시은 범주와 상관없이(학교 이름과 분리)
        return "hard", "학교·학적 질문"
    m = PAY_WORD_RE.search(t)
    if m and (not pay_soft or PAY_OWNER_RE.search(t)):
        return "hard", f"약속·돈·결정({m.group(0)})"
    for level, rx in (("hard", HARD_COMMIT_RE), ("decide", DECIDE_COMMIT_RE), ("avail", AVAIL_COMMIT_RE)):
        m = rx.search(t)
        if m:
            return level, f"약속·돈·결정({m.group(0).strip()})"
    m = TIMED_ASK_RE.search(t)
    if m and _DAY_OR_CLOCK_RE.search(t):
        return "avail", f"약속·돈·결정({m.group(0).strip()})"
    return "", ""


def pre_gate(text: str, st: Store, category: str | None = None, pay_soft: bool = True) -> str:
    """보류해야 하면 이유(짧은 한국어), 아니면 빈 문자열. 단계 구분 없이 걸리는지만(예전 동작)."""
    return gate(text, st, category, pay_soft)[1]


# ── 예/아니오 풀기(사용자 결정 11/08) ──
# OWNER 줄: fetch_messages 형식 '[11/08 12:00] 나: 내용' (시각 없는 '나: 내용'도)
OWNER_LINE_RE = re.compile(r"^(?:\[[^\]]*\]\s*)?나:\s?(.*)$", re.S)
# 시각·날짜 표현(숫자 없이 쓰는 것). 숫자는 따로 본다
TIME_WORD_RE = re.compile(
    r"오늘|내일|모레|글피|주말|평일|이번\s?주|다음\s?주|담주|[월화수목금토일]요일|아침|점심|저녁|밤|새벽|오전|오후|정오|자정"
    r"|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|열한|열두)\s?(?:시|분|시간)|반\s?시간|만\s?원|천\s?원")
# 상대가 구체적인 시각을 내민 말('9~10시면', '3시 반', '14:00') → 완곡 답도 안 하고 그대로 보류(새 시각)
CONCRETE_TIME_RE = re.compile(r"\d{1,2}\s?(?:시|:\d{2})|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|열한|열두)\s?시(?![간작])")
# 'AI야? 봇이야?' — 풀지 않는다(거짓말 금지, 보류)
AI_QUESTION_RE = re.compile(r"(?<![A-Za-z])(?:ai|bot|gpt)(?![A-Za-z])(?!\s?(?:관|과|학|대학|센터|융합|동|실|수업|강의|챌린지|대회|공모전|동아리|팀|프로젝트|플젝|스터디|툴|도구|모델|서비스|앱))|에이아이|봇|자동\s?응답|지피티|클로드|사람\s?(?:맞|이야|이에요|인가|이냐|임)", re.I)
# round5(DIAG §4-4): 보류 판정용 좁은 꼴. '로봇 동아리'·'봇 만들어봤어?'·'디스코드봇 플젝'·'그 사람이야?'는 빼고
# '너 봇이야?'·'로봇이야?'·'자동응답이야?'·'사람 맞아?'는 잡는다. 거짓말 막기(bot.ai_denial)는 넓은 AI_QUESTION_RE를 그대로 쓴다
_AI_HOLD_BOT = (r"(?<!로)봇(?!\s?(?:만들|만드|만들어|개발|짜|짰|코딩|플젝|프로젝트|동아리|대회|코드|서버|토큰|api|API|기능|명령어))"
                r"|로봇\s?(?:이|아|야|냐|임|맞|인가|인지|인거|아니)")
AI_HOLD_RE = re.compile(
    r"(?<![A-Za-z])(?:ai|bot|gpt)(?![A-Za-z])(?!\s?(?:관|과|학|대학|센터|융합|동|실|수업|강의|챌린지|대회|공모전|동아리|팀|프로젝트|플젝|스터디|툴|도구|모델|서비스|앱|만들|개발))"
    r"|에이아이|" + _AI_HOLD_BOT + r"|자동\s?응답|지피티|클로드"
    r"|(?<!그\s)(?<!저\s)(?<!그)(?<!저)(?<!어떤\s)(?<!그런\s)(?<!이런\s)(?<!저런\s)사람\s?(?:맞|이야|이에요|인가|이냐|임)", re.I)


def ai_hold_re():
    """보류 판정에 쓸 'AI냐' 정규식. ontology_card.AI_HOLD_NARROW=False면 예전 넓은 꼴(bot이 config ai_hold_narrow로 바꿈)."""
    return AI_HOLD_RE if AI_HOLD_NARROW else AI_QUESTION_RE


AI_HOLD_NARROW = True
# 완곡한 답에 들어가면 안 되는 수락어(확답으로 읽힘)
SOFT_BANNED_RE = re.compile(
    r"ㄱㄴ|ㄱㄱ|ㅇㅋ|ㅇㅇ|오케|오키|(?<![A-Za-z])ok(?![A-Za-z])|콜|좋아|좋습|좋죠|좋지|좋네|괜찮|가능|돼|됩니다|되죠|되지|되요|될\s?(?:것|듯|거)|됨"
    r"|넵|넹|넴|네네|갈게|갈께|갈\s?수|갈래|참석|지원할|할게요|그러자|그래|당연|물론|알겠|있(?:어|어요|습니다|죠|지|음)(?![가-힣])", re.I)
SOFT_YES_START_RE = re.compile(r"^\s*(?:네|예|응|웅|어\s|ㅇ|그럼|그치|맞아)")
# 완곡한 답이라면 '미룬다'는 말이 들어 있어야 한다
SOFT_DEFER_RE = re.compile(r"확인|알아보|생각해|이따|나중에|좀\s?있다|있다가|이따가|말해\s?줄|말씀\s?드릴|알려\s?줄|알려\s?드릴|연락|답해\s?줄|답\s?드릴|잠만|잠시만|잠깐만|보고\s?말")
# 미루는 말 틀(줄기 + 붙는 꼬리 '해보고/할게/드릴게요'). 이걸 뺀 나머지에 수락·약속 동사가 남으면 섞인 답(11/09)
SOFT_DEFER_FRAME_RE = re.compile("(?:" + SOFT_DEFER_RE.pattern + ")(?:해|하|보|봐|볼|봅|고|구|서|게|께|줄|드릴|드리|할|겠|습|니다|요|어|아)*")
SOFT_ACCEPT_LEFT_RE = re.compile(r"보자|갈게|갈께|갈\s?수|참여|참석|맞출|맞춰|될\s?(?:거|것|듯)|할게|할께|콜")
# 미루는 말 틀을 빼고 남아도 되는 말(B1 보강: 레드팀 확답성 19문장 중 12개가 통과하던 문제 — 금지어 대신 허용어로 본다)
SOFT_NEUTRAL_TOKEN_RE = re.compile(
    r"(?:아|어|앗|엇|오|음|흠|헐|아하|아아|으음"
    r"|좀|한번|한|번|일단|우선|먼저|다시|바로|금방|곧|따로|혹시|더|좀더"
    r"|내가|제가|나|저|나도|저도|그거|그건|그것|그것도|이거|이건|그|거|것|그쪽|요건|그부분|부분"
    r"|일정|스케줄|시간표|상황|사정|달력|캘린더"
    r"|(?:말|얘기|애기|답|여쭤|물어|체크)(?:해|하|할|보|봐|볼|고|게|께|줄|드릴|드리|겠|습|니다|요|어|아|서)*"
    r"|요|용|여|염|ㅋ+|ㅎ+|ㅠ+|ㅜ+)"
    r"(?:을|를|은|는|이|가|도|만|좀)?")
# B7(11/09): OWNER 자신의 경험·상태를 단정하는 말('안 해봤어', '해봤지', '없어', '몰라', '아직 안') — 근거가 있어야 한다
STATE_CLAIM_RE = re.compile(
    r"(?:안|못)\s?(?:해|써|들어|가|봐)\s?봤|(?:해|써|들어|가)\s?봤(?:어|지|음|는데|다|어요|습니다)|해\s?본\s?적"
    r"|안\s?했|했(?:어|지|음|는데|다|어요|습니다)(?![가-힣]*\?)|없(?:어|음|는데|다|어요|습니다)(?![가-힣])"
    r"|몰라|모르겠|모름|모릅니다|아직\s?(?:안|못)")
# 받은 말이 OWNER 자신의 경험·상태를 묻는가('해 봤냐', '써 봤어?', '들어?', '수강해?', '알아?')
SELF_Q_RE = re.compile(
    r"(?:해|써|들어|가|봐)\s?(?:봤|봄|본\s?적)|(?:해|써|들어|가)\s?보(?:셨|신|세요|시나)|(?:알|아)(?:아|냐|니|세요|시나요)\s?\?|아는\s?거|"
    r"있(?:어|냐|니|나|음|으세요|으신가요)\s?\?|했(?:어|냐|니|음|나|어요)\s?\?|해\s?봄\??|듣(?:냐|니|어|나요)|들어\s?\?|수강|"
    r"신청\s?했|지원\s?했|하고\s?있")
_SELF_STOP = {"혹시", "요즘", "그거", "이거", "저거", "OWNER", "OWNER아", "OWNER이", "너도", "너는", "니가", "네가", "형도", "형은", "혹시나",
              "근데", "그럼", "아직", "이제", "지금", "오늘", "어제", "한번", "진짜", "제대로", "직접", "같이", "그냥", "혹쉬"}


def self_fact_problem(text: str, reply: str, facts: str) -> str:
    """B7: 답이 OWNER 자신의 상태를 단정하는데('안 해봤어'·'해봤지'·'없어'·'몰라') 받은 말이 OWNER의 경험·상태를 묻고,
    그 주제가 근거(profile.slim.md 공개 절 + 대화 속 OWNER 본인 말 + 찾아본 OWNER 카톡)에 없으면 이유, 아니면 "".
    근거 판단: 받은 말의 주제어(물음 꼴·흔한 말 뺀 두 글자 이상) 중 하나라도 근거 글에 있으면 근거 있음."""
    m = STATE_CLAIM_RE.search(reply or "")
    if not m or not SELF_Q_RE.search(text or ""):
        return ""
    body = SELF_Q_RE.sub(" ", text or "")
    toks = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]+|[가-힣]{2,}", body) if t not in _SELF_STOP]
    low = (facts or "").lower()
    for t in toks:
        t = t.lower()
        if t in low or (len(t) >= 3 and re.fullmatch(r"[가-힣]+", t) and t[:-1] in low):
            return ""
    return f"자기 사실 근거 없음({m.group(0)})"


# round4(11/09, round3b 리플레이 1번): 묻지 않았는데 붙는 OWNER 자기 소개·지금 하는 일 묘사
# ('…정도고, 지금 동아리 팀 프로젝트를 하고 있습니다.'). 프로필에 근거가 있어도 받은 말이 묻지 않았으면 덧붙이지 않는다.
SELF_ACT_NOUN_RE = re.compile(
    r"프로젝트|플젝|동아리|스터디|연구|개발|인턴|알바|아르바이트|대회|ctf|해커톤|과제|수업|강의|학원|공부|대학원|회사|연구실|랩실"
    r"|창업|외주|부트캠프|자격증|시험|보안", re.I)
SELF_ACT_PROG_RE = re.compile(
    r"[가-힣]고\s?있(?:어|어요|습니다|는데|음|다|지|거든|구요|고)?|하는\s?중|중이(?:에요|예요|야|라|어서|거든|고|었)|중입니다"
    r"|(?:준비|진행|참여|활동|작업|수강|재학|휴학|개발)\s?중|(?:하|다니|듣|만들|배우)는\s?(?:중|편)")
SELF_INTRO_RE = re.compile(
    r"(?:저는|전|나는|난|제가|내가)\s?[^.!?~\n]{0,25}(?:학생|전공|학년|재학|휴학|복학|학과|과\s?(?:이에요|예요|입니다|이야|다녀))")
# 받은 말이 OWNER의 지금 하는 일·근황·소개를 묻는가
SELF_NOW_Q_RE = re.compile(
    r"요즘|요새|최근|근황|지금\s?(?:뭐|머|모|무슨)|뭐\s?(?:하|해|함)|머\s?(?:하|해)|뭐하|하고\s?있|하는\s?중|중이(?:야|에요|세요|신가|냐|니)"
    r"|다니(?:세요|냐|니|시나|는\s?(?:곳|데))|소개|누구(?:야|세요|신가|냐)|어떤\s?(?:분|사람|일|활동)|무슨\s?(?:일|활동|과|전공|공부)"
    r"|전공|학년|학과|어디\s?(?:다녀|학교|소속)|소속")
# 받은 말이 OWNER의 경험을 묻는가('해본 경험 있으세요?') — 지금 하는 일 묘사가 답의 전부면 그건 경험 답이라 지우지 않는다
SELF_EXP_Q_RE = re.compile(r"경험|해\s?보(?:셨|신|셨어)|해\s?봤|해\s?본\s?적|써\s?보(?:셨|신)|써\s?봤|써\s?본\s?적")
# 받은 말이 어떤 활동의 상태를 묻는가('플젝 어때?') — 그 활동 낱말이 답 문장에도 있으면 물은 것으로 본다
SELF_STATE_Q_RE = re.compile(r"어때|어떰|어떠|어떻|할\s?만|괜찮|잘\s?(?:돼|되)|힘들|빡세|재밌|재미")
# 문장 나누기: 마침표류 뒤, 또는 말끝 뒤에 '지금/요즘/저는…'으로 새 말이 시작될 때(구두점 없는 카톡 말)
_SD_SPLIT_RE = re.compile(r"(?<=[.!?~])\s+|(?<=[어요다야지음네죠까군걸중ㅋㅎ])\s+(?=(?:지금|요즘|요새|현재|최근|저는|전|나는|난|저도|나도)\s)"
                          r"|(?<=,)\s*(?=(?:지금|요즘|요새|현재)\s)")   # round5: '…정도고, 지금 동아리…'도 나눈다
# round5(DIAG §4-2): 경험 질문에 답한 경험 말('만들어 본 정도'·'경험은 적어요')이 든 절은 지우지 않는다
SELF_EXP_WORD_RE = re.compile(r"(?:해|만들어|써|들어|가)\s?(?:본|봤)|경험|적어요|적어|적습니다|처음이")


# round5(공개 방 답, round4 오픈채팅 #8 '저도 아직 배우는 중이에요', #4 '그건 제가 잘 모르겠네요~'):
# 공개 방(단톡·오픈채팅)에서는 활동 낱말이 없어도 OWNER 자기 상태 단정(배우는 중·뉴비·초보·잘 모름·처음·해 봤다/안 해 봤다)을 묻지 않았으면 뺀다
PUBLIC_SELF_STATE_RE = re.compile(
    r"(?:저도|저는|전|제가|나도|나는|난|내가)\s?[^.!?~\n]{0,20}?"
    r"(?:뉴비|초보|입문|문외한|학생|배우는\s?중|공부\s?중|배우고\s?있|공부하고\s?있|잘\s?(?:몰라|모르|모름)|모르겠|몰라|모릅니다"
    r"|처음(?:이|이에요|이라|입니다|봐|봤|들어|듣)|(?:안|못)\s?(?:해|써|들어|가|봐)\s?봤|(?:해|써|들어|가)\s?봤|경험\s?(?:이|은)?\s?(?:없|적|많))"
    r"|아직\s?(?:배우|공부|뉴비|초보|입문|잘\s?모)|(?:배우|공부하)는\s?중"
    r"|^\s*(?:그건|그거는|그거|거기까진|그쪽은)?\s?(?:잘\s?)?(?:모르겠|몰라요|모릅니다)")   # 주어 없는 '그건 잘 모르겠네요'(r5 #4·#16)


def _self_desc_unit(unit: str, text: str, public: bool = False) -> bool:
    if SELF_INTRO_RE.search(unit):
        return True
    if public and PUBLIC_SELF_STATE_RE.search(unit):
        return True
    if not (SELF_ACT_PROG_RE.search(unit) and SELF_ACT_NOUN_RE.search(unit)):
        return False
    if SELF_STATE_Q_RE.search(text or ""):   # '플젝 어때?'에 '지금 하고 있는데 재밌어'는 물은 것
        low = (text or "").lower().replace("플젝", "프로젝트")
        if any(n.lower().replace("플젝", "프로젝트") in low for n in SELF_ACT_NOUN_RE.findall(unit)):
            return False
    return True


def unasked_self_desc(text: str, reply: str, public: bool = False) -> tuple[str, list[str]]:
    """답에서 묻지 않은 OWNER 자기 소개·지금 하는 일 묘사 문장을 뺀다. (남은 답, 뺀 문장들).
    받은 말이 근황·하는 일·소개를 물었으면 아무것도 빼지 않는다. 남은 답에 뜻 있는 말이 없으면 남은 답은 "".
    public(단톡·오픈채팅, round5): OWNER 자기 상태 단정(PUBLIC_SELF_STATE_RE)도 뺀다 — 받은 말이 OWNER 경험·상태를
    물었으면(SELF_Q_RE·SELF_EXP_Q_RE) 이 추가 규칙은 쓰지 않는다(그때는 self_fact_problem이 근거를 본다)."""
    if not reply or SELF_NOW_Q_RE.search(text or ""):
        return reply or "", []
    if public and (SELF_Q_RE.search(text or "") or SELF_EXP_Q_RE.search(text or "")):
        public = False
    exp_q = bool(SELF_EXP_Q_RE.search(text or ""))
    polite = bool(re.search(r"요|니다", reply))
    kept_lines, cut = [], []
    for line in reply.split("\n"):
        units = [u for u in _SD_SPLIT_RE.split(line) if u and u.strip()]
        keep = []
        for u in units:
            if _self_desc_unit(u, text, public) and not (exp_q and SELF_EXP_WORD_RE.search(u) and not SELF_INTRO_RE.search(u)):
                cut.append(u.strip())
                if keep and keep[-1].endswith(","):   # '…본 정도고,' 뒤 절을 지웠으면 말끝을 닫는다('정도고요')
                    k = keep[-1].rstrip(",").rstrip()
                    keep[-1] = k + "요" if polite and k.endswith("고") else k
            else:
                keep.append(u.strip())
        if keep:
            if keep[-1].endswith(","):
                keep[-1] = keep[-1].rstrip(",").rstrip()
            kept_lines.append(" ".join(keep))
    if not cut:
        return reply, []
    kept = "\n".join(kept_lines).strip()
    core = re.sub(r"[\s~!?.,…ㅋㅎㅠㅜ^♡:;)(]+", "", kept)
    if len(re.findall(r"[가-힣A-Za-z0-9]", core)) < 2 or re.fullmatch(r"(?:근데|그리고|그래서|아|오|음|어)+", core):
        if SELF_EXP_Q_RE.search(text or "") and not any(SELF_INTRO_RE.search(c) for c in cut):
            return reply, []   # 경험을 물었고 답이 지금 하는 일뿐이면 그게 답(r4 리플레이 1번 과잉 삭제)
        kept = ""
    return kept, cut


# 인용이 '받은 말'과 이어지는지 볼 때 무시하는 흔한 두 글자(어미·조사)
_STOP_BIGRAMS = {"혹시", "하시", "하실", "있으", "으신", "신가", "나요", "까요", "가요", "세요", "해요", "니다", "습니", "합니",
                 "주세", "시나", "실까", "할까", "거죠", "거죵", "인가", "는데", "어요", "아요", "이에", "에요", "예요", "거야",
                 "하는", "하고", "해서", "하면", "그럼", "그거", "이거", "저거", "OWNER", "님이", "님은", "실거", "할거", "하겠"}


def _norm(s: str) -> str:
    return re.sub(r"[\s~!?.,…ㅎㅋㅠㅜ^♡]+", "", s or "")


def _bigrams(s: str) -> set[str]:
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", s or "")
    return {t[i:i + 2] for i in range(len(t) - 1)} - _STOP_BIGRAMS


_LINE_TS_RE = re.compile(r"^\[(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})\]")


def _line_time(line: str, now):
    """'[MM/DD HH:MM] ...' 줄의 시각(now와 같은 시간대, 연도는 now 기준으로 추정). 없으면 None."""
    m = _LINE_TS_RE.match(line or "")
    if not m:
        return None
    mo, d, h, mi = (int(x) for x in m.groups())
    try:
        t = now.replace(month=mo, day=d, hour=h, minute=mi, second=0, microsecond=0)
    except ValueError:
        return None
    if t - now > dt.timedelta(days=1):   # 12/31 줄을 1/1에 보면 작년
        try:
            t = t.replace(year=t.year - 1)
        except ValueError:
            return None
    return t


def owner_lines(ctx: list[str], max_age_h: float | None = None, now=None) -> list[str]:
    """[대화 기록]에서 OWNER('나')이 쓴 말만. max_age_h를 주면 그 시간 안에 쓴(시각이 적힌) 줄만."""
    if max_age_h is not None and now is None:
        now = dt.datetime.now().astimezone()
    out = []
    for line in ctx or []:
        # B4(11/09): bot.fetch_messages가 준 줄(owner 속성)은 user_id로 정한 화자만 믿는다 — '나:' 글자는 위조될 수 있다
        if getattr(line, "owner", None) is False:
            continue
        m = OWNER_LINE_RE.match(line or "")
        if not (m and m.group(1).strip()):
            continue
        if max_age_h is not None:
            t = _line_time(line, now)
            if t is None or (now - t) > dt.timedelta(hours=max_age_h):
                continue
        out.append(m.group(1).strip())
    return out


# B16(11/09 round2 리플레이 32번): 상대가 자기·제3자 얘기를 하다가 '어떰?'처럼 짧게 물을 때 봇이 OWNER 자기 말('그 과목은 어려워')을
# 되풀이해 답한 것. 상대 쪽 주어·계획 표지 + 평가 질문이면 대상이 OWNER이 아닐 수 있어 되풀이 답을 막는다(보류)
OTHER_SUBJECT_RE = re.compile(
    r"(?<![가-힣])(?:나도|나는|난|내가|나한테|저도|저는|전|제가|저한테|우리\s?(?:애|형|누나|언니|오빠|동생|엄마|아빠))(?![가-힣])"
    r"|생각이었|생각인데|하려고|할려고|가려고|갈려고|들으려|들을\s?(?:건|거|까|생각)|갈\s?(?:건|거)\s?(?:데|라)|할\s?(?:건|거)\s?(?:데|라)"
    r"|(?<![가-힣])(?:걔|걘|쟤|걔네)(?:는|가|도|한테|랑)?(?![가-힣])"
    r"|(?<![가-힣])(?:그\s?(?:분|사람|친구|애)|친구|형|누나|언니|오빠|동생|엄마|아빠|선배|후배|교수님)(?:은|는|이|가|도|한테)(?![가-힣])")
TARGET_Q_RE = re.compile(r"어떰|어때|어떠|어떻|어땠|어떤\s?(?:거|게|편)|어케|할\s?만|괜찮(?:아|나|냐|을까|겠|은가)|빡세|힘들(?:어|까|겠|나|냐)|쉬워|쉽나|어려워|어렵나|낫(?:냐|나|지)|추천|별로|ㄱㅊ")


def restate_target_problem(text: str, ctx: list | None, window: int = 8) -> str:
    """OWNER 말 되풀이 답을 막아야 하면 이유, 아니면 "". 받은 말(과 최근 상대 줄 몇 개)이 평가 질문이고,
    상대 줄에 상대 자신·제3자 주어나 상대의 계획 표지가 있으면 '물은 대상이 OWNER이 아닐 수 있음'."""
    t = text or ""
    lines = list(ctx or [])[-window:]
    other = [_body(str(x)) for x in lines if getattr(x, "owner", None) is False
             or (getattr(x, "owner", None) is None and not OWNER_LINE_RE.match(str(x) or ""))]
    burst = []   # 받은 말 바로 앞의 상대 연속 줄(마지막 OWNER 줄 뒤)
    for x in reversed(lines):
        if getattr(x, "owner", None) is True or (getattr(x, "owner", None) is None and OWNER_LINE_RE.match(str(x) or "")):
            break
        burst.append(_body(str(x)))
    if not (TARGET_Q_RE.search(t) or any(TARGET_Q_RE.search(b) for b in burst[:2])):
        return ""
    for b in [t, *other]:
        m = OTHER_SUBJECT_RE.search(b or "")
        if m:
            return f"물은 대상이 OWNER이 아닐 수 있음({m.group(0).strip()})"
    return ""


def _body(line: str) -> str:
    """'[11/08 12:00] 이름: 내용' → 내용."""
    return re.sub(r"^(?:\[[^\]]*\]\s*)?[^:]{1,40}:\s?", "", line or "", count=1)


# 부정어: 인용과 답 중 한쪽에만 있으면 뜻이 뒤집힌다(11/09)
NEG_RE = re.compile(r"(?<![가-힣])(?:안|못)(?![녕내전에쪽경부심정건과])|아니|아닌|아님|아냐|않|없|말자|ㄴㄴ")
# 되풀이 답에서 내용어로 치지 않는 흔한 말(맞장구·인사·호칭·어미)
RESTATE_COMMON = ("넵", "네네", "넹", "넴", "예예", "응응", "감사", "고마", "봬요", "뵈요", "뵐게", "뵙겠", "봐요", "그럼", "그래",
                  "맞아", "맞습", "맞죠", "저도", "제가", "저는", "나도", "내가", "나는", "일단", "혹시", "아마", "오키", "알겠",
                  "그거", "이거", "그렇", "네요", "해요", "합니다", "입니다", "이요", "요요", "우리", "님도", "ㅎㅎ", "ㅋㅋ", "아하", "아아")


def _content_words(s: str) -> list[str]:
    """2글자 이상 한글 낱말(숫자+단위 '9시'는 빼고, 흔한 말 제외)."""
    s = re.sub(r"\d+\s?(?:시간|시|분|일|월|명|개|번|원|주|년)?", " ", s or "")
    return [w for w in re.findall(r"[가-힣]{2,}", s) if not w.startswith(RESTATE_COMMON)]


def restate_problem(quote: str, owner_msgs: list[str], text: str, reply: str) -> str:
    """'OWNER이 이미 한 말 되풀이' 답을 보내도 되면 "", 아니면 이유.
    인용은 OWNER 줄에 그대로 있어야 하고(공백·문장부호 무시, 4글자 이상), 받은 말과 내용어 두 글자가 겹쳐야 하며,
    부정어(안/못/아니/없)는 인용과 답에 같이 있거나 같이 없어야 하고, 답의 내용어(흔한 말 제외)는 인용에 있어야 하며,
    답의 숫자·시각 표현은 인용에 있던 것만, 돈·제출 말은 없어야 한다."""
    q = _norm(quote)
    if len(q) < 4:
        return "인용 없음·짧음"
    if not any(q in _norm(m) for m in owner_msgs):
        return "인용이 OWNER 말에 없음"
    if not (_bigrams(quote) & _bigrams(text)):
        return "인용이 받은 말과 무관"
    if bool(NEG_RE.search(quote)) != bool(NEG_RE.search(reply or "")):
        return "인용과 답의 부정이 다름"
    nq = _norm(quote)
    for w in _content_words(reply or ""):
        if w[:2] not in nq:
            return f"답에 인용에 없는 말({w})"
    for n in re.findall(r"\d+", reply or ""):
        if n not in quote:
            return f"답에 새 숫자({n})"
    for w in TIME_WORD_RE.findall(reply or ""):
        if _norm(w) not in _norm(quote):
            return f"답에 새 시각({w})"
    if HARD_COMMIT_RE.search(_strip_time(reply or "")) or PAY_WORD_RE.search(reply or ""):
        return "답에 돈·제출"
    if ai_hold_re().search(text or ""):
        return "AI냐는 질문"
    return ""


def _strip_time(s: str) -> str:
    return re.sub(r"몇\s?시", "", s)


def soft_reply_problem(reply: str, text: str = "") -> str:
    """확답 없는 완곡한 답을 보내도 되면 "", 아니면 이유. 수락어·숫자·시각·긴 답은 안 된다."""
    r = (reply or "").strip()
    if not r:
        return "빈 답"
    if len(r) > 40:
        return "완곡 답이 김"
    if re.search(r"\d", r):
        return "숫자 있음"
    if TIME_WORD_RE.search(r):
        return "시각 표현 있음"
    m = SOFT_BANNED_RE.search(r)
    if m:
        return f"수락어({m.group(0)})"
    if SOFT_YES_START_RE.search(r):
        return "'네/응'으로 시작"
    m = STATE_CLAIM_RE.search(r)
    if m:   # B7: 미루는 답에 '안 해봤어/없어/몰라' 같은 OWNER 상태 단정이 섞이면 안 된다(dm_replay 21번: 해 본 상태였음)
        return f"상태 단정({m.group(0)})"
    if not SOFT_DEFER_RE.search(r):
        return "미루는 말이 없음"
    rest = SOFT_DEFER_FRAME_RE.sub(" ", r)   # 미루는 말 틀('확인해보고', '말해줄게')을 빼고 남은 말에 수락·약속이 있으면 거절
    m = SOFT_ACCEPT_LEFT_RE.search(rest)
    if m:
        return f"미루는 말 + 수락·약속({m.group(0)})"
    left = [w for w in re.findall(r"[가-힣ㄱ-ㅎㅏ-ㅣA-Za-z]+", rest) if not SOFT_NEUTRAL_TOKEN_RE.fullmatch(w)]
    if left:   # '꼭 도와줄게', '될걸', '거의 확실해', '빠질 일 없어'처럼 미룸 밖에 다른 말이 남으면 확답일 수 있어 거절
        return f"미루는 말 밖에 다른 말({left[0]})"
    if ai_hold_re().search(text or ""):
        return "AI냐는 질문"
    if CONCRETE_TIME_RE.search(text or ""):
        return "상대가 구체 시각을 냄(새 시각)"
    return ""


def _contact_name(st: Store, chat_id: str) -> str:
    c = st.data["contacts"].get(chat_id) or {}
    return c.get("name") or "상대"


def render_card(st: Store, chat_id: str, category: str, name: str = "", limit: int | None = None) -> str:
    """이 상대에게 보여도 되는 사실만으로 만든 카드. 값은 코드가 고른다(LLM이 고르지 않음)."""
    limit = limit or st.schema.get("limits", {}).get("card_chars", 900)
    vis = st.visible_facts(chat_id, category)
    name = name or _contact_name(st, chat_id)
    by = {}
    for f in vis:
        by.setdefault((f["subject"], f["predicate"]), []).append(f)

    def vals(subj, pred):
        return [f["value"] for f in by.get((subj, pred), [])]

    lines = [f"[상대 카드] {name} — 범주 {category}"]
    rel = vals(f"c:{chat_id}", "relation_label")
    role = vals(f"c:{chat_id}", "their_role")
    hon = vals(f"c:{chat_id}", "honorific_mine")
    addr = vals(f"c:{chat_id}", "address_term")
    if rel or role:
        lines.append("- 관계: " + " / ".join(role + rel))
    else:
        lines.append("- 관계: 기록 없음(추측하지 말 것)")
    if hon or addr:
        lines.append("- 말투: " + " ".join(hon + ([f"호칭 '{addr[0]}'"] if addr else [])))
    shared = [g for gid, g in st.data["groups"].items() if chat_id in g.get("members", [])]
    if shared:
        for g in shared:
            r = vals(g["id"], "my_role")
            s = vals(g["id"], "status")
            lines.append(f"- 함께 하는 곳: {g['name']}" + (f" (OWNER: {r[0]})" if r else "") + (f", 상태: {s[0]}" if s else ""))
    else:
        lines.append("- 함께 하는 곳: 기록 없음")
    last = vals(f"c:{chat_id}", "last_thread")
    if last:
        lines.append(f"- 최근 일: {last[-1]}")
    me = vals("me", "interest") + [st.data["groups"][g]["name"] for g in vals("me", "role_in") if g in st.data["groups"]
                                    and any(x["id"] == g for x in shared)]
    core = ["[나 핵심]", "- OWNER."]
    if not school_unresolved(st) and category in SCHOOL_SHARE_CATEGORIES:
        core.append(f"- 학교: {school_value(st)} (학년·전공·학적 변동은 말하지 않는다)")
    else:
        core.append("- 학교·학년·전공: 이 상대에게는 말하지 않는다.")
    if me:
        core.append("- 관심·활동: " + ", ".join(dict.fromkeys(me)))
    courses, programs, acts = vals("me", "course"), vals("me", "program"), vals("me", "activity")
    if courses:
        core.append("- 이번 학기 수업: " + ", ".join(courses[:8]))
    if programs:
        core.append("- 참여 프로그램: " + "; ".join(programs[:6]))
    if acts:
        core.append("- 요즘 하는 것: " + ", ".join(acts[:6]))
    notes = vals("me", "notice")
    if notes:
        core.append("- 알고 있는 공지: " + " / ".join(notes[:5]))
    core.append("- 정해진 약속: 봇은 모른다. 일정·약속은 확정하지 않는다.")
    core.append("- " + GENERIC_PRIVATE)
    core_txt = "\n".join(core)[: st.schema.get("limits", {}).get("me_core_chars", 350) + 600]
    card = "\n".join(lines)
    out = card + "\n" + core_txt
    if len(out) > limit:   # 자르기 순서: 최근 일 → 함께 하는 곳 → 관계는 남김
        out = out[:limit]
    return st.mask(out)


BENIGN_RE = re.compile(r"건강\s?(?:챙기|조심|하세요|하시|히|잘)|건강한\s?하루")   # 인사말은 비공개 주제가 아님


def output_check(reply: str, st: Store, chat_id: str, category: str | None = None) -> str:
    """보낼 답 검사. 문제면 이유, 아니면 빈 문자열. 상대가 먼저 꺼낸 단어라도 예외 없음(DESIGN §13 미해결 3)."""
    r = BENIGN_RE.sub("", reply or "")
    if st.never_hit(r):
        return "답에 개인정보 패턴"
    w = st.topic_hit(r)
    if w:
        return "답에 비공개 주제 어휘"
    low = r.lower()
    if (school_unresolved(st) or category not in SCHOOL_SHARE_CATEGORIES) and any(tok in low for tok in SCHOOL_TOKENS):
        return "답에 학교 이름"
    for cid, c in st.data["contacts"].items():
        n = c.get("name") or ""
        if cid != chat_id and len(n) >= 2 and n not in ("(알 수 없음)",) and n in r:
            return "답에 다른 상대 이름"
    return ""
