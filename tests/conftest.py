"""pytest로 tests 폴더를 돌릴 때(py -3.12 -m pytest tests -q) 스크립트형 테스트도 함께 돌린다(11/11).

test_bot.py·test_aside_bridge.py 등은 'py -3.12 -I tests\\test_bot.py'로 직접 돌리게 만든 스크립트라(맨 위에서 검사하고
sys.exit, pytest 함수 없음) pytest가 import하면 INTERNALERROR로 멈추거나, 같은 프로세스에서 bot 전역을 바꿔 다른 테스트를 흔든다.
그래서 pytest 함수(def test_)도 unittest.TestCase도 없는 test_*.py는 따로 프로세스(py -I)로 실행하고,
종료 코드가 0이 아니거나 'FAIL'로 시작하는 줄이 있으면 실패로 본다. pytest 함수로 쓴 파일은 그대로 pytest가 모은다."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
PYTEST_STYLE_RE = re.compile(r"^\s*def test_|^class \w+\(.*TestCase\)", re.M)
FAIL_LINE_RE = re.compile(r"^(?:FAIL\b|.*\bSOME FAILED\b)")


def _is_script(path: Path) -> bool:
    try:
        return not PYTEST_STYLE_RE.search(path.read_text(encoding="utf-8"))
    except OSError:
        return False


def pytest_pycollect_makemodule(module_path, parent):
    p = Path(module_path)
    if p.name.startswith("test_") and _is_script(p):
        return ScriptFile.from_parent(parent, path=p)
    return None


class ScriptFailed(Exception):
    pass


class ScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name=self.path.stem)


class ScriptItem(pytest.Item):
    def runtest(self):
        r = subprocess.run([sys.executable, "-I", str(self.path)], cwd=BASE, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=1800)
        lines = ((r.stdout or "") + (r.stderr or "")).splitlines()
        tail = [l for l in lines if l.strip()][-3:]
        fails = [l for l in lines if FAIL_LINE_RE.match(l)]
        self.add_report_section("call", "script", "\n".join(tail))
        if r.returncode != 0 or fails:
            raise ScriptFailed(f"종료 코드 {r.returncode}\n" + "\n".join(fails[:20] + tail))

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ScriptFailed):
            return f"{self.path.name} 실패:\n{excinfo.value}"
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"script: {self.path.name}"
