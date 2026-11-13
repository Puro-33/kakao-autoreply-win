"""PC 알림(Windows 토스트) — 봇이 OWNER 모르게 멈추는 일을 PC 화면으로만 알린다(11/09).

- 카톡·메신저로는 절대 보내지 않는다(봇은 답장만, 먼저 보내기 금지). 이 모듈에는 발신 코드가 없다.
- 외부 설치 없이 Windows PowerShell 5.1의 WinRT([Windows.UI.Notifications])로 띄운다(BurntToast 불필요).
- 실패하면(Windows 아님·PowerShell 없음·알림 꺼짐) 조용히 로그만 남긴다.
- 같은 종류(kind) 알림은 cooldown(기본 1시간)에 한 번만.

시험: py -3.12 -I notify.py "제목" "내용"
"""
from __future__ import annotations

import base64
import collections
import logging
import subprocess
import sys
import threading
import time

log = logging.getLogger("bot")

COOLDOWN_SEC = 3600.0
# 시작 메뉴 바로가기가 있는 Windows PowerShell의 AppUserModelID(따로 등록하지 않아도 토스트가 뜬다)
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_LAST: dict[str, float] = {}
_LOCK = threading.Lock()
HISTORY: collections.deque = collections.deque(maxlen=50)   # (시각, kind, 제목, 내용, 띄움 여부) — !상태·시험용


def _xml(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _script(title: str, body: str) -> str:
    # XML 이스케이프로 작은따옴표가 남지 않으므로 PowerShell 작은따옴표 문자열에 그대로 넣어도 된다
    xml = ('<toast><visual><binding template="ToastGeneric">'
           f'<text>{_xml(title)}</text><text>{_xml(body)}</text></binding></visual></toast>')
    return ("$ErrorActionPreference='Stop'\n"
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
            "$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
            f"$x.LoadXml('{xml}')\n"
            "$t = [Windows.UI.Notifications.ToastNotification]::new($x)\n"
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($t)\n")


def _show(title: str, body: str) -> bool:
    """토스트를 실제로 띄운다. 성공 True. 실패는 로그만."""
    if sys.platform != "win32":
        log.info("PC 알림(Windows 아님, 로그만): %s — %s", title, body)
        return False
    enc = base64.b64encode(_script(title, body).encode("utf-16-le")).decode("ascii")
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-EncodedCommand", enc], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("PC 알림 실패(%s): %s — %s", e, title, body)
        return False
    if p.returncode != 0:
        log.warning("PC 알림 실패(rc=%s %s): %s — %s", p.returncode, (p.stderr or "").strip()[:200], title, body)
        return False
    return True


def due(kind: str, cooldown: float | None = None, now: float | None = None) -> bool:
    """이 종류 알림을 지금 띄워도 되는가(쿨다운이 지났는가). 세지는 않는다."""
    now = time.time() if now is None else now
    cd = COOLDOWN_SEC if cooldown is None else cooldown
    with _LOCK:
        return now - _LAST.get(kind, -1e18) >= cd


def toast(kind: str, title: str, body: str, cooldown: float | None = None, now: float | None = None,
          wait: bool = False) -> bool:
    """PC 토스트 알림. 쿨다운에 걸리면 False(안 띄움). 띄우기는 배경 스레드에서(호출한 작업을 막지 않게).
    wait=True면 띄운 결과를 기다린다(시험용)."""
    now = time.time() if now is None else now
    cd = COOLDOWN_SEC if cooldown is None else cooldown
    title = " ".join(str(title).split())[:60]
    body = " ".join(str(body).split())[:200]
    with _LOCK:
        if now - _LAST.get(kind, -1e18) < cd:
            log.info("PC 알림 쿨다운으로 생략 [%s] %s", kind, title)
            return False
        _LAST[kind] = now
    log.warning("PC 알림 [%s] %s — %s", kind, title, body)

    def run():
        ok = False
        try:
            ok = _show(title, body)
        except Exception as e:  # noqa: BLE001
            log.warning("PC 알림 예외: %s", e)
        HISTORY.append((now, kind, title, body, ok))

    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    a = sys.argv[1:] + ["카톡 봇 알림 시험", "이 알림이 보이면 토스트가 됩니다"][len(sys.argv[1:]):]
    print("ok" if _show(a[0], a[1]) else "fail")
