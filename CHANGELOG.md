# Changelog

## 0.1.1 — 창을 띄우지 않는 전송

- 전송을 버튼 클릭에서 입력칸 Enter로 바꿈(`pc_kakao_send.deliver`). 대화창이 최소화된 채로 나가고 포커스가 바뀌지 않는다.
  0.6초 안에 입력칸이 안 비면 예전 버튼 클릭으로 넘어간다.
- 방 열기(`open_chat`)가 메인 창을 꺼내지 않는다(숨김·최소화인 채로 검색). 안 열릴 때만 활성화 없이 꺼내 다시 한다.
- 새로 연 대화창은 바로 최소화하고 쓰던 창을 다시 앞에 둔다. 닫지 않으므로 같은 방의 다음 전송은 방 열기가 없다.
- 셔임(`iris_native.py`)이 전송마다 단계별 시간을 출력한다(글 내용·방 이름 제외).
- 실측(나와의 채팅): 열린 방 0.02~0.16초, 닫힌 방 2.7~3.2초. 닫힌 방을 열 때 대화창이 0.15~0.33초 보이는 것은 남아 있다.

## 0.1.0 — 첫 공개

- Windows 네이티브 경로: PC 카카오톡 로컬 DB 읽기(`kakao_native`), Iris 호환 셔임(`iris_native.py`),
  PC 창 전송(`pc_kakao_send.py`), 실행·감시 스크립트(`start_native.py`, `start_native.ps1`).
- 명령줄 도구 `kakaowin.py`: `status`, `chats`, `messages`, `scan`, `send`(기본 미리보기, `--yes`, `--me`), `serve`, `bot`.
- 키 캐시를 DPAPI로 묶어 저장소 밖(`%LOCALAPPDATA%\kakao-autoreply-win`)에 저장.
- 시험 잠금 기본값(`native_send_only_memo: true`): 나와의 채팅에만 전송.
- 자동 답장 봇(`bot.py`)과 선택 경로(LDPlayer + Iris, `start.ps1`).
