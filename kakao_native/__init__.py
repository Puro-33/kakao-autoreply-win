# -*- coding: utf-8 -*-
"""
kakao_native — LDPlayer 없이, 이 PC에 로그인된 '본인' 카카오톡 데스크톱의
로컬 대화 DB를 직접 읽기 위한 모듈.

범위 제한:
  - 오직 "지금 이 PC에서 실행 중인, 이 PC 사용자 본인 계정"의 KakaoTalk.exe와
    그 본인 소유 로컬 DB(%LOCALAPPDATA%\\Kakao\\KakaoTalk)만 대상으로 한다.
  - 다른 사람의 계정, 다른 PC, 원격 대상, 전달받은 DB 파일을 상대로 동작하도록
    만들지 않는다. 개인 자동화(내 카톡 봇이 내 최근 대화를 읽는 것) 전용이다.
  - 이 패키지는 메시지 전송·DB 수정·프로세스 변조를 하지 않는다. 전부 읽기 전용이다
    (전송은 저장소 루트의 pc_kakao_send.py 가 PC 카톡 창으로 한다).

구성:
  sqlcipher4.py      : raw 키 기반 페이지 복호화/키 검증(포맷 2종 프리셋, WAL 적용, cryptography 사용)
  memkey.py          : 실행 중인 본인 KakaoTalk.exe 메모리에서 DB 키 후보 복원(ctypes, 읽기 전용)
  account.py         : 방 목록·사용자 이름·메시지 읽기(키 제공자 + DPAPI 키 캐시)
  dpapi.py           : 키 캐시를 이 PC·이 Windows 계정에 묶어 저장
  dbreader.py        : 복호화한 chatLogs_*.edb에서 최근 메시지 읽기
  format_analysis.py : 암호화 DB 구조 통계(키 없이)
  cli.py             : 프로세스 탐색 → 키 복원 → 복호화 → 최근 대화 확인(자가검증)
"""

from .sqlcipher4 import (
    SqlCipher4Params,
    CipherParams,
    SQLCIPHER4_DEFAULTS,
    DEVICE_AES128_DEFAULTS,
    FORMAT_PRESETS,
    FormatDetection,
    SQLITE_MAGIC,
    PAGE_SIZES,
    detect_format,
    derive_hmac_key,
    verify_raw_key,
    valid_sqlite_header,
    matching_formats,
    decrypt_database,
)

__all__ = [
    "SqlCipher4Params",
    "CipherParams",
    "SQLCIPHER4_DEFAULTS",
    "DEVICE_AES128_DEFAULTS",
    "FORMAT_PRESETS",
    "FormatDetection",
    "SQLITE_MAGIC",
    "PAGE_SIZES",
    "detect_format",
    "derive_hmac_key",
    "verify_raw_key",
    "valid_sqlite_header",
    "matching_formats",
    "decrypt_database",
]
