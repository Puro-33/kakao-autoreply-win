# -*- coding: utf-8 -*-
"""카톡 봇을 돌린다: iris_native 셔임(PC DB 수신·PC 창 전송) + bot.py.

    py -3.12 start_native.py            (또는  py -3.12 kakaowin.py bot)

흐름: PC 카톡 DB → iris_native(폴링·/query·/reply) → bot.py(답장 판정) → PC 카톡 창.
config.json 의 iris_url 은 셔임 주소(http://127.0.0.1:3001)여야 한다.
주의: 실제 자동응답이 시작된다. PC 카카오톡이 켜져(로그인) 있어야 한다.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import iris_native


def main(keys_mode: str = "auto") -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    cfg_path = Path(__file__).resolve().parent / "config.json"
    if not cfg_path.exists():
        print("config.json 이 없습니다. config.example.json 을 복사해 만드세요."); return 1
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    port = int(cfg.get("iris_url", "http://127.0.0.1:3001").rsplit(":", 1)[1].split("/")[0])
    threading.Thread(target=lambda: iris_native.serve(port=port, keys_mode=keys_mode), daemon=True).start()
    for _ in range(180):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/config", timeout=3).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    else:
        print("셔임이 뜨지 않았습니다(PC 카톡 로그인 상태 확인)."); return 1
    print(f"iris_native 셔임 가동(:{port}). 봇을 시작합니다.")
    import bot
    bot.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
