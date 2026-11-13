# Changelog

## 0.1.0 — 첫 공개

- Windows 네이티브 경로: PC 카카오톡 로컬 DB 읽기(`kakao_native`), Iris 호환 셔임(`iris_native.py`),
  PC 창 전송(`pc_kakao_send.py`), 실행·감시 스크립트(`start_native.py`, `start_native.ps1`).
- 명령줄 도구 `kakaowin.py`: `status`, `chats`, `messages`, `scan`, `send`(기본 미리보기, `--yes`, `--me`), `serve`, `bot`.
- 키 캐시를 DPAPI로 묶어 저장소 밖(`%LOCALAPPDATA%\kakao-autoreply-win`)에 저장.
- 시험 잠금 기본값(`native_send_only_memo: true`): 나와의 채팅에만 전송.
- 자동 답장 봇(`bot.py`)과 선택 경로(LDPlayer + Iris, `start.ps1`).
