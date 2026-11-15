# -*- coding: utf-8 -*-
"""kakaowin — PC 카카오톡(Windows)용 명령줄 도구. 내 PC 카톡만 쓴다(에뮬레이터 불필요).

    py -3.12 kakaowin.py status                       # 준비 상태(카톡 실행·계정 폴더·키·설정)
    py -3.12 kakaowin.py chats [--limit 30] [--json]  # 방 목록(최근순)
    py -3.12 kakaowin.py messages --chat "방 이름" [--since 7d] [--limit 50] [--json]
    py -3.12 kakaowin.py scan [--chat "방 이름"] [--deep]   # 키가 없는 방 DB의 키를 카톡 메모리에서 찾기
    py -3.12 kakaowin.py send "방 이름" "글"            # 기본은 미리보기(dry-run). 실제 전송은 --yes
    py -3.12 kakaowin.py send --me "글" --yes          # 나와의 채팅으로 보내 시험
    py -3.12 kakaowin.py serve [--port 3001]          # Iris 호환 셔임만 띄우기(bot.py 가 붙음)
    py -3.12 kakaowin.py bot                          # 셔임 + 자동응답 봇(start_native.py)

안전 기본값
  - send 는 --yes 가 없으면 보내지 않는다(어떤 창에 어떻게 넣을지만 보여 준다).
  - config.json 이 없거나 native_send_only_memo 가 true(예시 기본값)면 '나와의 채팅' 말고는 보내지 않는다.
  - 채널(PlusChat)에는 보내지 않는다. 글은 1~500자. 입력칸에 쓰던 초안이 있으면 덮어쓰지 않는다.
  - 보낸 뒤에는 카톡 DB에 내 글이 실제로 들어갔는지 확인한다(입력칸이 비워진 것만으로는 성공으로 보지 않는다).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAX_LEN = 500


def _out(obj, as_json: bool, text: str) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1) if as_json else text)


def parse_since(s: str | None) -> int:
    """'7d' '12h' '30m' 또는 'YYYY-MM-DD' → unix 초. 없으면 0."""
    if not s:
        return 0
    m = re.fullmatch(r"(\d+)\s*([dhm])", s.strip())
    if m:
        n, u = int(m.group(1)), m.group(2)
        return int(time.time()) - n * {"d": 86400, "h": 3600, "m": 60}[u]
    try:
        return int(time.mktime(time.strptime(s.strip(), "%Y-%m-%d")))
    except ValueError:
        raise SystemExit(f"--since 형식을 모르겠습니다: {s} (예: 7d, 12h, 2026-01-31)")


def load_cfg() -> dict | None:
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def send_lock_on(cfg: dict | None) -> bool:
    return True if cfg is None else bool(cfg.get("native_send_only_memo", True))


def _account(args, scan: bool = True):
    from kakao_native.account import Account
    quiet = (lambda *a: None) if getattr(args, "json", False) else (lambda *a: print(*a, file=sys.stderr))
    return Account(keys_mode=args.keys, log=quiet, scan=scan)


# ───────────────────────── 명령 ─────────────────────────
def cmd_status(args) -> int:
    from kakao_native.account import find_account_dir, app_home
    info: dict = {"python": sys.version.split()[0], "platform": sys.platform}
    try:
        import cryptography
        info["cryptography"] = cryptography.__version__
    except ImportError:
        info["cryptography"] = None
    try:
        from kakao_native.memkey import find_kakaotalk_pids
        info["kakaotalk_running"] = bool(find_kakaotalk_pids())
    except Exception as e:  # noqa: BLE001
        info["kakaotalk_running"] = f"확인 실패({type(e).__name__})"
    acc = find_account_dir()
    info["account_dir_found"] = bool(acc)
    info["key_cache"] = (app_home() / "keys.dpapi").exists()
    try:
        import kakao_native.keystore_v2  # noqa: F401
        info["v2_keystore_module"] = True
    except ImportError:
        info["v2_keystore_module"] = False
    cfg = load_cfg()
    info["config_json"] = cfg is not None
    info["send_lock_memo_only"] = send_lock_on(cfg)
    try:
        import pc_kakao_send as PK
        info["chat_windows_open"] = len(PK.list_chat_windows())
        info["main_window"] = bool(PK.find_main_window())
    except Exception as e:  # noqa: BLE001
        info["chat_windows_open"] = f"확인 실패({type(e).__name__})"
    ok = info["cryptography"] and info["account_dir_found"] and info["kakaotalk_running"] is True
    lines = [f"{k:22} {v}" for k, v in info.items()]
    lines.append("준비됨" if ok else "준비 안 됨: README '설치'를 보세요")
    _out(info, args.json, "\n".join(lines))
    return 0 if ok else 1


def cmd_chats(args) -> int:
    from kakao_native.account import TYPE_KO, ts
    a = _account(args)
    rooms = a.rooms[: args.limit]
    data = [{"id": r["id"], "type": r["type"], "name": r["name"], "members": r["member_count"],
             "last": r["last"], "readable": a.readable(r["id"])} for r in rooms]
    text = "\n".join(f"{ts(r['last'], '%m-%d %H:%M')}  {TYPE_KO.get(r['type'], r['type']):8} "
                     f"{'읽기가능' if r['readable'] else '키없음 '}  {r['name']}  ({r['members']}명)" for r in data)
    _out(data, args.json, text or "방이 없습니다")
    return 0


def _one_room(a, name: str) -> dict:
    hits = a.find_rooms(name)
    if len(hits) != 1:
        near = [r["name"] for r in hits[:8]]
        raise SystemExit(f"방 '{name}'을(를) {'찾지 못함' if not hits else f'{len(hits)}개 찾음: {near}'}")
    return hits[0]


def cmd_messages(args) -> int:
    from kakao_native.account import ts
    a = _account(args)
    room = _one_room(a, args.chat)
    if not a.readable(room["id"]):
        a.scan_rooms([room["id"]])
    if not a.readable(room["id"]):
        print("이 방의 DB 키를 아직 찾지 못했습니다. PC 카톡에서 그 방을 한 번 연 뒤 다시 하거나 'scan --deep'을 쓰세요.",
              file=sys.stderr)
        return 4
    msgs = a.messages(room, limit=args.limit, since=parse_since(args.since))
    text = "\n".join(f"[{ts(m['at'])}] {m['sender']}: {m['text']}" for m in msgs)
    _out({"room": room["name"], "messages": msgs}, args.json, text or "(메시지 없음)")
    return 0


def cmd_scan(args) -> int:
    a = _account(args)
    ids = [_one_room(a, args.chat)["id"]] if args.chat else None
    n = a.scan_rooms(ids, deep=args.deep)
    readable = sum(a.readable(r["id"]) for r in a.rooms)
    print(f"새로 찾은 키 {n}개 · 읽을 수 있는 방 {readable}/{len(a.rooms)}")
    return 0


def confirm_in_db(a, room: dict, body: str, since: int, wait: float = 20.0) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s or "").strip()[:40]  # noqa: E731
    want = norm(body)
    end = time.time() + wait
    while True:
        if any(m["mine"] and norm(m["text"]) == want for m in a.messages(room, 10, since)):
            return True
        if time.time() >= end:
            return False
        time.sleep(2)


def cmd_send(args) -> int:
    body = (args.text or "").strip()
    if not 1 <= len(body) <= MAX_LEN:
        print(f"글 길이 {len(body)}자: 1~{MAX_LEN}자만 보냅니다."); return 1
    a = _account(args)
    if args.me:
        room = a.memo_room()
        if room is None:
            print("나와의 채팅 방을 찾지 못했습니다."); return 1
        # 나와의 채팅 창 제목은 '나와의 채팅'이 아니라 내 프로필 이름이다
        title = a.names.get(a.my_id) or room["title"] or room["name"]
    else:
        if not args.room:
            print("방 이름이 필요합니다(또는 --me)."); return 1
        room = _one_room(a, args.room)
        title = room["title"] or room["name"]
    if room["type"] == "PlusChat":
        print("채널 방에는 보내지 않습니다."); return 1
    if args.yes and send_lock_on(load_cfg()) and room["type"] != "MemoChat":
        print("전송 잠금: config.json 의 native_send_only_memo 가 true(또는 config.json 없음)라 나와의 채팅만 보냅니다.")
        return 1
    import pc_kakao_send as PK
    if not args.yes:
        r = PK.send(title, body, send=False, auto_open=True)
        print(f"[미리보기] 방: {room['name']} ({room['type']}) / {len(body)}자 — 보내지 않음. 실제로 보내려면 --yes")
        for s in r.get("steps", []):
            print("  -", s)
        if not r.get("ok"):
            print("  준비 안 됨:", r.get("error"))
        return 0 if r.get("ok") else 1
    since = int(time.time()) - 5
    r = PK.send(title, body, send=True, auto_open=True)
    if not r.get("ok"):
        print("전송 실패(Enter 전):", r.get("error")); return 1
    if not a.readable(room["id"]):
        a.scan_rooms([room["id"]])
    if a.readable(room["id"]) and confirm_in_db(a, room, body, since):
        print(f"전송 확인: 카톡 DB에 내 글이 들어갔습니다({r.get('timings_ms', {}).get('total', '?')}ms)."); return 0
    print("전달 미확인: 전송 버튼까지 눌렀지만 20초 안에 카톡 DB에서 내 글을 확인하지 못했습니다(다시 보내지 마세요).")
    return 1


def cmd_serve(args) -> int:
    import iris_native
    iris_native.serve(port=args.port, webhook=args.webhook, keys_mode=args.keys)
    return 0


def cmd_bot(args) -> int:
    import start_native
    return start_native.main(keys_mode=args.keys)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kakaowin", description="PC 카카오톡(Windows) 읽기·보내기·자동응답")
    ap.add_argument("--keys", choices=("auto", "memory", "v2"), default="auto",
                    help="DB 키를 구하는 방법(기본 auto: v2 선택 모듈이 있으면 v2, 없으면 메모리)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="준비 상태 점검"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("chats", help="방 목록"); p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_chats)
    p = sub.add_parser("messages", help="한 방의 최근 메시지"); p.add_argument("--chat", required=True)
    p.add_argument("--since"); p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_messages)
    p = sub.add_parser("scan", help="키가 없는 방 DB의 키를 카톡 메모리에서 찾기"); p.add_argument("--chat")
    p.add_argument("--deep", action="store_true", help="느리지만 더 넓게(raw 윈도)"); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("send", help="글 한 건 보내기(기본 미리보기)"); p.add_argument("room", nargs="?")
    p.add_argument("text", nargs="?"); p.add_argument("--me", action="store_true", help="나와의 채팅으로")
    p.add_argument("--yes", action="store_true", help="실제로 보낸다"); p.set_defaults(fn=cmd_send)
    p = sub.add_parser("serve", help="Iris 호환 셔임만 실행"); p.add_argument("--port", type=int, default=3001)
    p.add_argument("--webhook"); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("bot", help="셔임 + 자동응답 봇 실행"); p.set_defaults(fn=cmd_bot)
    return ap


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    args = build_parser().parse_args(argv)
    if args.cmd == "send" and args.me and args.room and not args.text:
        args.text, args.room = args.room, None   # send --me "글"
    try:
        return args.fn(args)
    except RuntimeError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
