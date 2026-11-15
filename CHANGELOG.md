# Changelog

## 0.3.0 — LDPlayer 경로 제거

- 안드로이드 에뮬레이터(LDPlayer) + Iris 경로를 지웠다. 남은 경로는 하나다: PC 카카오톡 로컬 DB를 복호화해 읽고, 창 메시지로 보낸다.
- `start.ps1`(에뮬레이터 실행·adb 포트 연결·Iris 기동) 삭제. 실행은 `start_native.ps1` 또는 `kakaowin.py bot`.
- `bot.py`에서 adb 호출(포트 복구, Iris 재시작, adb 상태 확인)을 걷어냈다. `!상태`와 요약의 'adb' 항목도 없앴다.
- 설정 `adb_path`, `adb_serial`, `iris_auto_restart`, `native_iris` 삭제(남아 있어도 무시).

## 0.2.0 — 말투 학습 제거, 읽기·전송 다듬기

- 말투 학습을 뺌: `voice_corpus.py`, `style_profile.py`, `learn_room.py`와 그 평가 스크립트를 지우고, 답을 주인 말투로 고쳐 쓰던
  단계(말투 거울, AI 티 검사 후 다시 쓰기, 하루 한 번 말투 프로필 갱신)를 `bot.py`에서 걷어냈다. 설정 `voice_mode`,
  `dm_rhythm_rooms`는 없어졌다(남아 있어도 무시).
- 높임(반말·해요체·합쇼체) 검사는 남김: 학습 없이 말끝만 보는 `register.py`로 옮겼다.
- DB 읽기: 복호화한 DB를 임시 파일 대신 메모리에서 연다. 예전에는 확인할 때마다 임시 폴더에 `-wal`/`-shm` 파일이 남았다.
  임시 파일로 물러설 때도 세 파일을 모두 지운다.
- DB 읽기: 방 DB 파일(`.edb`, `-wal`)의 수정 시각·크기가 그대로면 그 방은 복호화하지 않는다. 열지 못한 방은 읽은 것으로 치지 않고
  다음 확인 때 다시 읽는다(새 글을 놓치지 않는다).
- 전송: Enter가 전송이 아니라 줄바꿈으로 들어간 경우를 알아채고, 글을 되돌린 뒤 버튼으로 보낸다. 예전에는 보내지 않고 성공을 돌려줬다.
- 읽기는 로컬 DB, 전송은 창 메시지 방식을 그대로 둔다. 화면 자동화(UI Automation)·클립보드 붙여넣기·키 입력 흉내는 쓰지 않는다
  (포커스를 뺏고 클립보드를 덮어쓴다).

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
