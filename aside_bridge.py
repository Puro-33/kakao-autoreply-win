"""Aside 브라우저로 페이지를 '읽기만' 하는 다리 (2026-11-09).

Aside = 주인이 평소 쓰는 AI 브라우저(`aside` CLI). 주인의 로그인 쿠키가 그 브라우저에 살아 있으므로,
봇 전용 Playwright 프로필의 학교 세션이 풀렸을 때 ingest_sites.py 가 이걸로 같은 페이지를 한 번 더 읽는다.

- read_page(url): `aside repl` 한 번 안에서 openTab → 로드 대기 → 본문 innerText·링크 → 마지막 URL·제목 → closeTab.
  결과는 (상태, 텍스트, 마지막 URL) 3-튜플로 풀리고 .title·.links·.reason·.opened 도 가진다.
  상태: 'ok' | 'login_needed'(로그인 화면으로 튕김) | 'error'(.reason: robots·timeout·repl·parse·js·empty·unavailable·bad_url)
- 읽기만 한다: 만드는 JS에 클릭·입력·폼 제출·다운로드 코드가 없다. 로그인 화면이면 로그인하지 않고 'login_needed'.
- 같은 프로세스 안에서 요청 사이 최소 MIN_GAP_SEC(10초). robots.txt 조회도 같은 간격을 따른다.
  다른 경로(Playwright)로 방금 요청했으면 note_site_request()로 알려, Aside 첫 요청도 그때부터 10초 띄운다.
- robots.txt 가 막는 경로는 기본으로 열지 않는다(respect_robots=True → reason 'robots').
- `aside update`·설정 변경·메모리 수정·로그인 지시는 하지 않는다. stderr의 업데이트 안내 배너는 지운다.
- 표준 라이브러리만. 테스트는 RUNNER·ROBOTS_FETCH·find_exe·_now·_sleep 를 바꿔 끼운다(tests/test_aside_bridge.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MIN_GAP_SEC = 10.0
REPL_TIMEOUT = 150.0
MARK = "@@ASIDE_BRIDGE@@"
RC_TIMEOUT, RC_NOEXEC = 124, 127

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_BANNER = re.compile(r"^\s*Aside CLI \S+ is available|Run: aside update")
_STATUS = re.compile(r"^\[(ok|error)\s*\|\s*[\d.]+\s*m?s\]\s*$", re.M)
_LOGIN_PATH = re.compile(r"log-?in|sign-?in", re.I)
_LOGIN_WORD = re.compile(r"로그인|log ?in|sign ?in", re.I)

_last_at: float | None = None
_now = time.monotonic
_sleep = time.sleep


class PageResult(tuple):
    """read_page 결과. `status, text, final_url = r` 로 풀리고 r.title·r.links·r.reason·r.opened 도 있다."""

    def __new__(cls, status: str, text: str, final_url: str, *, title: str = "", links=(), reason: str = "",
                opened: bool = False):
        obj = tuple.__new__(cls, (status, text, final_url))
        obj.title, obj.links, obj.reason, obj.opened = title, list(links), reason, opened
        return obj

    status = property(lambda self: self[0])
    text = property(lambda self: self[1])
    final_url = property(lambda self: self[2])


def find_exe() -> str | None:
    """aside 실행 파일(PATH 우선, 없으면 %LOCALAPPDATA%\\Aside\\CLI\\current\\aside.exe). 없으면 None.
    Windows에선 .exe 만 받는다: PATH에 aside.cmd/.bat 래퍼가 먼저 있으면 cmd.exe 를 거쳐 JS 인자 속 |·&·> 가
    셸 문법으로 해석되므로(인자 리스트여도) 그런 래퍼는 건너뛴다."""
    p = shutil.which("aside")
    if p and (os.name != "nt" or p.lower().endswith(".exe")):
        return p
    base = os.environ.get("LOCALAPPDATA")
    if base:
        c = Path(base) / "Aside" / "CLI" / "current" / "aside.exe"
        if c.is_file():
            return str(c)
    return None


def _subprocess_runner(argv: list[str], timeout: float) -> tuple[int, str]:
    """기본 실행기: 셸 없이 인자 리스트로, 창 없이, stdin 닫고 실행. (종료 코드, stdout[+stderr])."""
    try:
        p = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return RC_TIMEOUT, ""
    except OSError:
        return RC_NOEXEC, ""
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out + ("\n" + err if err.strip() else "")


RUNNER = _subprocess_runner


def clean_output(text: str) -> str:
    """ANSI 색 코드와 'Aside CLI … is available … Run: aside update' 배너 줄을 지운다."""
    text = _ANSI.sub("", (text or "").replace("\r\n", "\n"))
    return "\n".join(ln for ln in text.split("\n") if not _BANNER.search(ln)).strip("\n")


def run_repl(code: str, timeout: float = REPL_TIMEOUT, runner=None) -> tuple[int, str]:
    """`aside repl <code>`(인자 리스트, 셸 문자열 아님). (종료 코드, 정리된 stdout).
    aside는 JS 예외에도 0으로 끝나고 끝에 '[error | Nms]'만 찍으므로, 그때는 종료 코드를 1로 바꿔 돌려준다.
    실행 파일이 없으면 (127, ''), 시간 초과면 (124, '')."""
    exe = find_exe()
    if not exe:
        return RC_NOEXEC, ""
    rc, out = (runner or RUNNER)([exe, "repl", code], timeout)
    out = clean_output(out)
    marks = _STATUS.findall(out)
    if rc == 0 and marks and marks[-1] == "error":
        rc = 1
    return rc, out


def aside_available(runner=None) -> bool:
    """aside 실행 파일이 있고 'aside account status' 가 signed in 이면 True.
    계정 정보(이메일 등)는 돌려주지도 기록하지도 않는다. 앱이 꺼져 있으면 read_page 가 'error'로 알려 준다."""
    exe = find_exe()
    if not exe:
        return False
    rc, out = (runner or RUNNER)([exe, "account", "status"], 30)
    low = clean_output(out).lower()
    if rc != 0 or "not signed in" in low or "signed out" in low:
        return False
    return "signed in" in low


def _wait_gap() -> None:
    """앞 요청이 끝난 뒤 MIN_GAP_SEC가 안 지났으면 남은 만큼 기다린다."""
    if _last_at is not None:
        left = MIN_GAP_SEC - (_now() - _last_at)
        if left > 0:
            _sleep(left)


def _mark() -> None:
    global _last_at
    _last_at = _now()


def note_site_request() -> None:
    """다른 경로(예: ingest_sites 의 Playwright)로 방금 사이트에 요청했음을 알린다.
    다음 Aside 요청(robots.txt 조회 포함)이 지금부터 MIN_GAP_SEC 뒤에 나가게 한다."""
    _mark()


def _split(url: str):
    """urlsplit 인데 깨진 주소(예: 'http://[x')면 예외 대신 None."""
    try:
        return urlsplit(url or "")
    except ValueError:
        return None


_ROBOTS: dict[str, list[tuple[bool, str]] | None] = {}


def _urllib_fetch(url: str, timeout: float) -> tuple[int, str]:
    """robots.txt 를 쿠키 없이 GET. (HTTP 상태, 본문). 네트워크 오류는 (0, '')."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (personal read-only robots check)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:  # noqa: BLE001
        return 0, ""


ROBOTS_FETCH = _urllib_fetch


def parse_robots(text: str) -> list[tuple[bool, str]]:
    """'User-agent: *' 묶음의 규칙만 [(허용?, 경로 패턴)]. 이 다리는 따로 봇 이름을 쓰지 않으므로 '*'만 본다."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    uas: list[str] = []
    rules: list[tuple[bool, str]] = []
    in_rules = False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if in_rules:
                groups.append((uas, rules))
                uas, rules, in_rules = [], [], False
            uas.append(v.lower())
        elif k in ("allow", "disallow"):
            in_rules = True
            if uas:
                rules.append((k == "allow", v))
    groups.append((uas, rules))
    return [r for g_uas, g_rules in groups if "*" in g_uas for r in g_rules]


def _rule_match(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    end = pattern.endswith("$")
    body = pattern[:-1] if end else pattern
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body) + ("$" if end else "")
    return re.match(rx, path) is not None


def robots_verdict(rules: list[tuple[bool, str]], path: str) -> bool:
    """RFC 9309: 맞는 규칙 중 가장 긴 것이 이기고, 길이가 같으면 허용이 이긴다. 맞는 규칙이 없으면 허용."""
    best: tuple[int, bool] | None = None
    for allow, pat in rules:
        if _rule_match(pat, path):
            key = (len(pat), allow)
            if best is None or key > best:
                best = key
    return True if best is None else best[1]


def robots_allows(url: str) -> bool:
    """robots.txt 가 이 URL(경로+쿼리)을 허용하면 True. 호스트마다 한 번만 받는다(이 프로세스 안).
    robots.txt 가 없으면(4xx) 허용, 못 받으면(5xx·네트워크) 모두 막힘으로 본다."""
    sp = _split(url)
    if not sp or not sp.netloc:
        return False
    origin = f"{sp.scheme}://{sp.netloc}"
    if origin not in _ROBOTS:
        _wait_gap()
        try:
            code, body = ROBOTS_FETCH(origin + "/robots.txt", 20)
        finally:
            _mark()
        if 200 <= code < 300:
            _ROBOTS[origin] = parse_robots(body)
        elif 400 <= code < 500:
            _ROBOTS[origin] = []
        else:
            _ROBOTS[origin] = None
    rules = _ROBOTS[origin]
    if rules is None:
        return False
    return robots_verdict(rules, (sp.path or "/") + (f"?{sp.query}" if sp.query else ""))


_PAGE_JS = """await (async () => {
  const o = {};
  let t = null;
  try {
    t = await openTab(__URL__);
    await new Promise((r) => setTimeout(r, __WAIT__));
    let d = null;
    for (let i = 0; i < 2 && !d; i++) {
      try {
        d = await t.evaluate(() => {
          const el = document.querySelector('main') || document.body;
          const pw = Array.from(document.querySelectorAll('input[type=password]')).filter((x) => x.getClientRects().length > 0).length;
          const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 400).map((a) => a.href);
          return { text: el ? String(el.innerText || '').slice(0, __LIM__) : '', pw: pw, links: links };
        });
      } catch (e) {
        if (i) throw e;
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
    o.url = t.url();
    try { o.title = await t.title(); } catch (e) { o.title = ''; }
    o.text = d.text; o.pw = d.pw; o.links = d.links;
  } catch (e) {
    o.err = String((e && e.message) || e).slice(0, 200);
  } finally {
    if (t) { try { await closeTab(t); } catch (e) { o.closeErr = String((e && e.message) || e).slice(0, 100); } }
  }
  console.log(__MARK__ + JSON.stringify(o));
})();"""


def build_page_js(url: str, wait_sec: float, limit: int) -> str:
    """read_page가 repl에 넘기는 JS. 열기·기다리기·읽기·닫기만 있다(클릭·입력·제출·다운로드 없음).
    최상위 const/let 이 없어(즉시 실행 함수 안) repl 세션의 이름 재사용 문제도 없다."""
    return (_PAGE_JS.replace("__WAIT__", str(max(0, int(wait_sec * 1000))))
            .replace("__LIM__", str(max(1000, int(limit) * 3)))
            .replace("__MARK__", json.dumps(MARK))
            .replace("__URL__", json.dumps(url)))


def _parse(out: str) -> dict | None:
    for line in reversed((out or "").splitlines()):
        i = line.find(MARK)
        if i >= 0:
            try:
                d = json.loads(line[i + len(MARK):])
            except json.JSONDecodeError:
                return None
            return d if isinstance(d, dict) else None
    return None


def norm_text(txt: str, limit: int) -> str:
    """ingest_sites.page_text 와 같은 정리: 공백·탭 하나로, 빈 줄 3개 이상은 2개로, 앞뒤 공백 떼고 limit자."""
    txt = re.sub(r"[ \t]+", " ", txt or "")
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()[:limit]


def looks_logged_out(final_url: str, title: str, text: str, pw_inputs: int, markers=()) -> bool:
    """로그인 화면으로 튕겼는지: URL 경로에 login/signin, 보이는 비밀번호 칸, 사이트별 표식(URL·본문 앞 1200자),
    또는 짧은 화면(400자 미만)에 '로그인/Log in/Sign in'(본문이나 제목)만 있는 경우."""
    fsp = _split(final_url)
    if _LOGIN_PATH.search(fsp.path if fsp else ""):
        return True
    if pw_inputs > 0:
        return True
    head = ((final_url or "") + "\n" + (text or "")[:1200]).lower()
    if any(m and m.lower() in head for m in markers):
        return True
    body = (text or "").strip()
    return len(body) < 400 and bool(_LOGIN_WORD.search(body) or _LOGIN_WORD.search(title or ""))


def same_site_links(links, base_url: str, patterns=None, cap: int = 400) -> list[str]:
    """base_url 과 같은 호스트의 http(s) 링크만, #조각 떼고, (patterns가 있으면 그 정규식에 맞는 것만) 중복 없이 순서대로.
    깨진 주소(urlsplit 이 ValueError)는 건너뛴다(한 링크 때문에 읽기 전체가 죽지 않게)."""
    bsp = _split(base_url)
    host = bsp.netloc.lower() if bsp else ""
    seen: set[str] = set()
    out: list[str] = []
    for h in links or []:
        if not isinstance(h, str):
            continue
        sp = _split(h)
        if not sp or not host or sp.scheme not in ("http", "https") or sp.netloc.lower() != host:
            continue
        h2 = urlunsplit((sp.scheme, sp.netloc, sp.path, sp.query, ""))
        if patterns and not any(re.search(p, h2) for p in patterns):
            continue
        if h2 not in seen:
            seen.add(h2)
            out.append(h2)
    return out[:cap]


def read_page(url: str, wait_sec: float = 3.0, *, markers=(), link_patterns=None, limit: int = 6000,
              respect_robots: bool = True, timeout: float = REPL_TIMEOUT, runner=None) -> PageResult:
    """Aside 브라우저에서 url 을 새 탭으로 열어 읽고 닫는다(repl 한 번). 앞 요청과 MIN_GAP_SEC 이상 띄운다.
    markers: 사이트별 '로그아웃 화면' 표식(URL·본문 앞부분). link_patterns: .links 에 남길 링크 정규식.
    돌려주는 값: PageResult('ok'|'login_needed'|'error', 텍스트, 마지막 URL)."""
    sp = _split(url)
    if not sp or sp.scheme not in ("http", "https") or not sp.netloc:
        return PageResult("error", "http(s) 주소가 아님", url, reason="bad_url")
    if not find_exe():
        return PageResult("error", "aside 실행 파일 없음", url, reason="unavailable")
    if respect_robots and not robots_allows(url):
        return PageResult("error", "robots.txt가 막는 경로라 열지 않음", url, reason="robots")
    _wait_gap()
    try:
        rc, out = run_repl(build_page_js(url, wait_sec, limit), timeout=timeout, runner=runner)
    finally:
        _mark()
    if rc == RC_NOEXEC:
        return PageResult("error", "aside 실행 실패", url, reason="unavailable")
    if rc == RC_TIMEOUT:
        return PageResult("error", f"aside repl {int(timeout)}초 초과", url, reason="timeout", opened=True)
    d = _parse(out)
    if d is None:
        return PageResult("error", f"aside repl 결과 없음(rc={rc})", url, reason="repl" if rc else "parse", opened=True)
    final = str(d.get("url") or url)
    title = str(d.get("title") or "")
    if d.get("err"):
        return PageResult("error", "페이지 열기 실패: " + str(d["err"])[:120], final, title=title, reason="js",
                          opened=True)
    text = norm_text(str(d.get("text") or ""), limit)
    try:
        pw = int(d.get("pw") or 0)
    except (TypeError, ValueError):
        pw = 0
    if looks_logged_out(final, title, text, pw, markers):
        return PageResult("login_needed", "", final, title=title, reason="login", opened=True)
    if not text:
        return PageResult("error", "본문이 비어 있음", final, title=title, reason="empty", opened=True)
    return PageResult("ok", text, final, title=title, links=same_site_links(d.get("links"), url, link_patterns),
                      opened=True)
