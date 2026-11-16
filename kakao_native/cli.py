"""
이 PC·본인 계정 전용: 실행 중인 KakaoTalk.exe 에서 키를 복원해
로컬 chatLogs_*.edb 를 직접 읽는 end-to-end 확인 도구.

실제 메시지 전송은 하지 않는다. 키/메시지 본문은 콘솔에 날것으로 찍지 않는다.
사용:  py -3.12 -m kakao_native.cli            (최근 대화 방 하나로 자가검증)
       py -3.12 -m kakao_native.cli --list     (방 DB 목록만)
"""

from __future__ import annotations

import argparse
import glob
import getpass
import json
import sqlite3
from dataclasses import replace
import os
import sys

from .sqlcipher4 import SQLCIPHER4_DEFAULTS, FORMAT_PRESETS, PAGE_SIZES
from .dbreader import read_recent, verify_database_file
from .format_analysis import analyze_directory

DEFAULT_USER_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Kakao", "KakaoTalk", "users"
)


def find_chat_dbs(user_root: str = DEFAULT_USER_ROOT) -> list[str]:
    """본인 계정 폴더의 chatLogs_*.edb 전부(수정시각 최신순)."""
    pat = os.path.join(user_root, "*", "chat_data", "chatLogs_*.edb")
    files = glob.glob(pat)
    pat2 = os.path.join(user_root, "*", "chatLogs_*.edb")
    files += [f for f in glob.glob(pat2) if f not in files]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def find_appstate(user_root: str = DEFAULT_USER_ROOT) -> str | None:
    """본인 계정 폴더의 v2 키 저장소 appstate.dat 경로."""
    hits = glob.glob(os.path.join(user_root, "*", "appstate.dat"))
    return hits[0] if hits else None


def account_dir(user_root: str = DEFAULT_USER_ROOT) -> str | None:
    hits = glob.glob(os.path.join(user_root, "*", "appstate.dat"))
    return os.path.dirname(hits[0]) if hits else None


def _first_page(path: str, size: int = 4096) -> bytes:
    with open(path, "rb") as f:
        return f.read(size)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="KakaoTalk PC 본인 DB 네이티브 읽기(개인용)")
    ap.add_argument("--list", action="store_true", help="방 DB 목록만 출력")
    ap.add_argument("--deep", action="store_true", help="hex 스캔 실패 시 raw 16/32바이트 윈도 스캔")
    ap.add_argument("--limit", type=int, default=3, help="최근 행 수")
    ap.add_argument("--max-dbs", type=int, default=8, help="키 못 찾으면 다음 방으로(시도 상한)")
    ap.add_argument("--analyze-dir", metavar="DIR",
                    help="read-only aggregate EDB structure measurements; no keys/processes")
    ap.add_argument("--verify-key", action="store_true",
                    help="prompt for an owned raw key; verify in RAM, no scan or DB copy")
    ap.add_argument("--db", help="one owned EDB path (required with --verify-key)")
    ap.add_argument("--format", choices=("auto", *FORMAT_PRESETS), default="auto",
                    help="키 검증: 지정 포맷만 검사; 메모리 스캔: 지정 포맷 우선, 두 프리셋 검사")
    ap.add_argument("--page-size", type=int, choices=PAGE_SIZES)
    ap.add_argument("--show-text", action="store_true",
                    help="본인 로컬 화면에서만: message 본문·보낸이·시각을 그대로 출력(평문인지 2차 암호인지 눈으로 확인용)")
    ap.add_argument("--schema-db", metavar="PATH",
                    help="특정 .edb(방목록 chatListInfo.edb·사용자 TalkUserDB.edb 등)의 키를 메모리에서 찾아 스키마만 출력(본문 없음)")
    ap.add_argument("--v2", action="store_true",
                    help="v2 키 저장소(appstate.dat)로 전체 방 키를 한 번에 복구: 메모리에서 루트 키 R 1회 → 전 방 DEK 0.3초")
    ap.add_argument("--rescan-root", action="store_true", help="저장된 R 캐시를 무시하고 메모리에서 다시 찾는다")
    args = ap.parse_args(argv)

    if args.analyze_dir:
        if args.verify_key or args.db or args.deep or args.list:
            ap.error("--analyze-dir cannot be combined with DB/key/scan/list options")
        try:
            report = analyze_directory(args.analyze_dir, args.page_size or 4096)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report["files_opened"] and not report["read_errors"] and not report["files_changed_during_read"] else 5
        except OSError as exc:
            print(f"Read-only analysis failed: {type(exc).__name__}", file=sys.stderr)
            return 5

    try:
        selected = None if args.format == "auto" else replace(
            FORMAT_PRESETS[args.format], page_size=args.page_size or 4096
        )
    except ValueError as exc:
        ap.error(str(exc))
    if args.verify_key:
        if not args.db or args.deep or args.list:
            ap.error("--verify-key requires --db and cannot combine with --deep/--list")
        if args.page_size and selected is None:
            ap.error("--page-size requires an explicit --format for key verification")
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = bytes.fromhex(getpass.getpass("Owned raw key (hex, hidden): ").strip())
            if len(key) not in (16, 32):
                raise ValueError("raw key must be 16 or 32 bytes")
            report = verify_database_file(args.db, key, selected)
            print(json.dumps(report, indent=2))
            print("Main DB image only; WAL is not replayed. No-HMAC is not authentication.")
            return 0
        except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError,
                getpass.GetPassWarning, EOFError) as exc:
            print(f"Key/database verification failed: {type(exc).__name__}", file=sys.stderr)
            return 6
    if args.db:
        ap.error("--db requires --verify-key")
    try:
        selected = selected or replace(SQLCIPHER4_DEFAULTS, page_size=args.page_size or 4096)
    except ValueError as exc:
        ap.error(str(exc))


    dbs = find_chat_dbs()
    if not dbs:
        print("chatLogs_*.edb 를 찾지 못했습니다(경로/계정 확인 필요).")
        return 2

    if args.list:
        print(f"방 DB {len(dbs)}개:")
        for p in dbs[:50]:
            print(f"  {os.path.getsize(p):>10,}B  {os.path.basename(p)}")
        return 0

    from .memkey import find_kakaotalk_pids, recover_key_for_db
    pids = find_kakaotalk_pids()
    if not pids:
        print("실행 중인 KakaoTalk.exe 가 없습니다. 카카오톡을 켜고(대화방을 한 번 열고) 재시도하세요.")
        return 3
    print(f"KakaoTalk.exe PID: {pids}")

    if args.v2:
        import time as _t
        try:
            from . import keystore_v2 as kv
        except ImportError:
            print("--v2 는 선택 모듈 kakao_native/keystore_v2.py 가 있어야 합니다. 없으면 --v2 없이 쓰세요."); return 2
        from .memkey import find_v2_root_key
        ap_path = find_appstate()
        if not ap_path:
            print("appstate.dat 를 찾지 못했습니다(계정 폴더 확인 필요)."); return 2
        ksv = kv.parse_appstate(ap_path)
        print(f"appstate.dat: 엔트리 {len(ksv.entries)}개")
        import hashlib as _h
        from .account import app_home
        cache = str(app_home() / "rootkey.dpapi")
        root = None if args.rescan_root else kv.load_root_key(cache, ksv)
        if root is not None:
            print(f"루트 키 R: 저장된 캐시 사용(DPAPI, sha256 앞 8자리 {_h.sha256(root).hexdigest()[:8]})")
        else:
            t0 = _t.time()
            found = find_v2_root_key(ksv, pids)
            if found is None:
                print("루트 키 R 을 메모리에서 찾지 못했습니다. 카카오톡을 켜 둔 채(로그인 상태) 재시도하세요."); return 4
            root, src = found
            print(f"루트 키 R 확인 (PID {src}, sha256 앞 8자리 {_h.sha256(root).hexdigest()[:8]}), 스캔 {_t.time()-t0:.1f}초")
            if kv.save_root_key(root, cache):
                print("  → R 을 DPAPI 로 저장(다음부터는 스캔 없이 0.3초. R 은 재시작 후에도 불변).")
        t1 = _t.time()
        deks = kv.recover_all_deks(root, ksv)
        print(f"전체 방 DEK 복구: {len(deks)}/{len(ksv.entries)}개, {_t.time()-t1:.2f}초")
        acc = account_dir() or ""
        chat = [(rp, dek) for rp, dek in deks.items() if rp.decode("utf-8", "replace").replace("\\", "/").split("/")[-1].startswith("chatLogs_")]
        print(f"chatLogs 방 {len(chat)}개 — 최근 수정순 상위 {min(args.max_dbs, len(chat))}개 읽기 시험:")
        paths = sorted(((os.path.join(acc, rp.decode("utf-8", "replace")), dek) for rp, dek in chat),
                       key=lambda x: os.path.getmtime(x[0]) if os.path.exists(x[0]) else 0, reverse=True)
        ok = 0
        for path, dek in paths[: args.max_dbs]:
            if not os.path.exists(path):
                continue
            try:
                info = read_recent(path, dek, limit=args.limit)
            except Exception as exc:
                print(f"  [읽기 실패] {os.path.basename(path)}: {type(exc).__name__}"); continue
            n = sum(c for _, c in info["tables"])
            ok += 1
            if args.show_text and info.get("recent"):
                import datetime as _dt
                print(f"  {os.path.basename(path)}: {info['chat_table']} 최근 {len(info['recent'])}행")
                for r in info["recent"]:
                    try:
                        tsv = _dt.datetime.fromtimestamp(int(r.get("sendAt") or 0)).strftime("%m-%d %H:%M")
                    except Exception:
                        tsv = str(r.get("sendAt"))
                    print(f"    [{tsv}] author={r.get('authorId')}: {r.get('message')!r}")
            else:
                print(f"  {os.path.basename(path)}: 테이블 {len(info['tables'])}종, 대화 {info.get('chat_table')} (행 {dict(info['tables']).get('chatLogs','?')})")
        print(f"\n읽기 성공 {ok}개. v2 일괄 키 복구 성공.")
        return 0

    if args.schema_db:
        path = args.schema_db
        if not os.path.isfile(path):
            print(f"파일이 없습니다: {path}"); return 2
        fp = _first_page(path, selected.page_size)
        rec = recover_key_for_db(fp, path, pids=pids, params=selected, deep_scan=args.deep)
        if rec is None:
            print(f"[키 못 찾음] {os.path.basename(path)} (카톡에서 해당 기능을 한 번 열고 --deep로 재시도)")
            return 4
        print(f"[키 확인] {os.path.basename(path)} (format: {rec.format_name})")
        info = read_recent(path, rec.enc_key, params=rec.params, limit=0)
        print(f"  테이블: {info['tables']}")
        for tname, cols in info.get("schema", {}).items():
            print(f"  {tname}: {cols}")
        return 0

    for path in dbs[: args.max_dbs]:
        fp = _first_page(path, selected.page_size)
        rec = recover_key_for_db(fp, path, pids=pids,
                                 params=selected, deep_scan=args.deep)
        if rec is None:
            print(f"[키 못 찾음] {os.path.basename(path)} (이 방을 최근 연 적 없거나 "
                  "후보·포맷·페이지 크기가 맞지 않음)")
            continue
        print(f"[키 확인] {os.path.basename(path)} <- PID {rec.source_pid} "
              f"(format: {rec.format_name}) "
              f"(key sha256 앞 8자리: {__import__('hashlib').sha256(rec.enc_key).hexdigest()[:8]})")
        info = read_recent(path, rec.enc_key, params=rec.params, limit=args.limit)
        print(f"  테이블: {info['tables']}")
        if info.get("chat_table") is None:
            for tname in ("chatLogs", "chat_logs"):
                if tname in info.get("schema", {}):
                    print(f"  {tname} 칼럼: {info['schema'][tname]}")
        print(f"  대화 테이블: {info['chat_table']} / 정렬: {info['order_col']}")
        if args.show_text:
            import datetime as _dt
            print(f"  최근 {len(info['recent'])}행(본문 그대로 — 본인 확인용):")
            for r in info["recent"]:
                try:
                    ts = _dt.datetime.fromtimestamp(int(r.get("sendAt") or 0)).strftime("%Y-%m-%d %H:%M")
                except (ValueError, OSError, OverflowError):
                    ts = str(r.get("sendAt"))
                print(f"    [{ts}] author={r.get('authorId')} type={r.get('type')}: {r.get('message')!r}")
        else:
            print(f"  최근 {len(info['recent'])}행(본문은 길이/타입만 표시):")
            for r in info["recent"]:
                preview = {}
                for k, v in r.items():
                    if isinstance(v, (bytes, bytearray)):
                        preview[k] = f"<bytes:{len(v)}>"
                    elif isinstance(v, str):
                        preview[k] = f"<str:{len(v)}>"
                    else:
                        preview[k] = f"<{type(v).__name__}>"
                print("   ", preview)
        print("\n본인 로컬 DB 직접 읽기 성공.")
        return 0

    print("시도한 방들에서 키를 찾지 못했습니다. 카카오톡에서 대화방을 열어 두고 재시도하세요.")
    return 4


if __name__ == "__main__":
    sys.exit(main())
