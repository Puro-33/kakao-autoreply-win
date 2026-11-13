# -*- coding: utf-8 -*-
"""Windows DPAPI 로 작은 비밀(복구한 DB 키 캐시)을 '이 PC·이 Windows 로그인 계정'에 묶어 암·복호화한다.

다른 PC나 다른 Windows 계정에서는 풀 수 없다. 표준 라이브러리(ctypes)만 쓴다. Windows 전용.
"""
from __future__ import annotations

import ctypes
import sys

ENTROPY = b"kakao-autoreply-win/keycache/v1"


def available() -> bool:
    return sys.platform == "win32"


def _call(data: bytes, unprotect: bool, entropy: bytes = ENTROPY) -> bytes | None:
    if not available():
        return None
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes) -> BLOB:
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    src, ent, out = blob(data), blob(entropy), BLOB()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptUnprotectData if unprotect else crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(data: bytes, entropy: bytes = ENTROPY) -> bytes | None:
    """평문 → DPAPI 블롭. 실패하면 None."""
    return _call(data, False, entropy)


def unprotect(blob: bytes, entropy: bytes = ENTROPY) -> bytes | None:
    """DPAPI 블롭 → 평문. 다른 PC·계정의 블롭이거나 깨졌으면 None."""
    return _call(blob, True, entropy)
