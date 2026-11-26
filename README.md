# kakao-autoreply-win

**PC 카카오톡(Windows)만으로 돌아가는 AI 카톡 자동 답장 봇과 명령줄 도구.**
An AI auto-reply bot and CLI for KakaoTalk on Windows — no Android emulator needed.

<p align="center"><img src="docs/demo.gif" width="720" alt="나와의 채팅에 보낸 메시지에 봇이 6.5초 만에 답하는 실제 화면 (이름은 가림)"></p>
<p align="center">나와의 채팅에 메시지를 넣으면 봇이 6.5초 만에 답합니다. 빨리 감기 없는 실제 속도예요. 전체 시연과 1회차 앞 3분은 <a href="https://kakao-bot-class.pages.dev/?from=github">강의 사이트</a>에서 가입 없이 볼 수 있습니다.</p>

> **따라 하기용 프롬프트 묶음(무료)**: 이 저장소를 받아 봇을 켜기까지, Claude Code에 순서대로 붙여 넣는 프롬프트 11개입니다.
> 설치 → 읽기 확인 → 나와의 채팅 전송 → 내 이름 넣기 → 봇 켜기 → 답장 다듬기 → 자동 시작 → 잠금 풀기 전 점검, 그리고 막혔을 때.
> 강의 사이트에 가입하면 받을 수 있습니다(결제 불필요). → **https://kakao-bot-class.pages.dev**
>
> **강의**: 이 봇을 Claude Code에게 시켜서 만든 과정을 세션 기록 원문으로 정리했습니다. 12회차와 실습, 제가 친 지시 163개가 그대로 나옵니다. 1회차는 가입하면 무료입니다.
> Free prompt pack and course (Korean): 11 copy-paste prompts for Claude Code that take you from `git clone` to a running bot. → https://kakao-bot-class.pages.dev

> [!NOTE]
> 이 프로젝트는 **내 PC에 있는 내 카카오톡 로컬 DB를 읽고**, **내 PC 카카오톡 창에 글을 넣어** 동작합니다.
> 카카오 서버·API를 부르지 않고, 통신 프로토콜(LOCO)을 분석하거나 재구현하지 않으며, 카카오톡 프로그램을 고치지 않습니다.
> 카카오와 아무 관계가 없는 비공식 개인 프로젝트입니다. 아래 [면책](#면책)을 꼭 읽어 주세요.

[한국어](#개요) | [English](#english)

---

## 개요

받은 카톡에 **자동으로 답장**하는 봇입니다. 사람이 버튼을 누르지 않아도 보내기 때문에,
노력의 대부분은 **무엇을 보내지 않을지를 코드가 정하는 데** 들어가 있습니다.
돈·약속·개인정보·결정이 걸린 메시지는 AI가 아니라 코드가 막고(보류) 주인의 '할 일'로 넘깁니다.

### 왜 만들었나

- 기존 카톡 자동화는 대부분 **안드로이드 에뮬레이터 + 루팅** 조합이 필요했습니다. 무겁고, 노트북 절전 후 자주 끊깁니다.
- 맥에는 로컬 DB를 읽는 CLI가 있지만, **Windows에서 읽기·보내기·AI 자동 답장을 한 번에** 하는 공개 도구는 찾기 어려웠습니다.
- 그래서 PC 카카오톡 하나로 **읽기(로컬 DB) → 판단(Claude CLI + 코드 관문) → 보내기(PC 창)** 를 잇는 경로를 만들었습니다.

## 시작하기

### 1) 설치

준비물

- Windows 10/11, [PC 카카오톡](https://www.kakaocorp.com/page/service/service/KakaoTalk) (본인 계정으로 로그인)
- Python 3.12 (`py -3.12`)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` 명령, 로그인된 상태) — 자동 답장 봇에만 필요

```powershell
git clone https://github.com/Puro-33/kakao-autoreply-win.git
cd kakao-autoreply-win
py -3.12 -m pip install -r requirements.txt
```

설정 파일을 복사합니다(복사본은 모두 `.gitignore`에 있어 올라가지 않습니다).

```powershell
copy config.example.json config.json
copy dm_categories.example.json dm_categories.json
copy reply_policy.example.md reply_policy.md
copy profile.example.md profile.md
```

템플릿과 코드 프롬프트의 `OWNER`를 주인 이름으로 바꿉니다
(`dm_categories.json`의 `owner_name`, `reply_policy.md`, `profile.md`, `bot.py`의 `OWNER_CALL_RE`, `ontology_card.py`의 `SCHOOL_TOKENS`).

### 2) 권한

- 별도 관리자 권한은 필요 없습니다. 같은 Windows 계정으로 실행 중인 `KakaoTalk.exe`의 메모리를 **읽기 전용**으로 열어
  내 로컬 DB 키를 찾습니다. 찾은 키는 Windows DPAPI로 **이 PC·이 Windows 계정에 묶어**
  `%LOCALAPPDATA%\kakao-autoreply-win\keys.dpapi` 에 저장합니다(저장소 폴더 밖).
- 보내기는 PC 카톡 창에 Win32 메시지로 글을 넣고 전송 버튼을 누르는 방식입니다. 보내는 동안 카톡 창이 잠깐 열릴 수 있습니다.

### 3) 동작 확인

```powershell
py -3.12 kakaowin.py status
```

`cryptography`, 카톡 실행 여부, 계정 폴더, 키 캐시, 설정 파일, 전송 잠금 상태를 보여 줍니다. 마지막 줄이 `준비됨`이면 됩니다.

### 4) 사용해 보기

```powershell
py -3.12 kakaowin.py chats --limit 10                  # 최근 방 10개
py -3.12 kakaowin.py messages --chat "방 이름" --since 1d
py -3.12 kakaowin.py send --me "테스트"                  # 미리보기만(보내지 않음)
py -3.12 kakaowin.py send --me "테스트" --yes            # 나와의 채팅으로 실제 전송
```

처음 실행 때는 메모리에서 키를 찾느라 몇 분 걸릴 수 있습니다(개발 PC 실측: 전체 메모리 1회 약 130초). 찾은 키는 캐시에 남아
다음부터는 바로 열립니다. `키없음`으로 나오는 방은 PC 카톡에서 그 방을 한 번 연 뒤 `py -3.12 kakaowin.py scan` 을 실행하세요.

## 명령

| 명령 | 하는 일 |
|---|---|
| `status [--json]` | 준비 상태 점검(카톡 실행·계정 폴더·키 캐시·설정·전송 잠금) |
| `chats [--limit N] [--json]` | 방 목록(최근순, 방 종류·읽기 가능 여부·인원) |
| `messages --chat "이름" [--since 7d\|12h\|2026-01-31] [--limit N] [--json]` | 한 방의 최근 메시지 |
| `scan [--chat "이름"] [--deep]` | 키가 없는 방 DB의 키를 카톡 메모리에서 다시 찾기(`--deep`은 느리지만 넓게) |
| `send "방" "글" [--yes]` | 글 한 건 보내기. **`--yes`가 없으면 미리보기만** 하고 보내지 않음 |
| `send --me "글" [--yes]` | 나와의 채팅으로 보내기(시험용) |
| `serve [--port 3001]` | Iris 호환 셔임만 실행(아래 '동작 원리') |
| `bot` | 셔임 + 자동 답장 봇 실행(`start_native.py`) |

모든 명령에 `--keys auto|memory|v2`를 줄 수 있습니다(기본 `auto`).

## 자동 답장 봇

### 실행

`config.json`의 `iris_url`은 `http://127.0.0.1:3001`로 둡니다(예시 기본값).
**`native_send_only_memo`는 예시 기본값이 `true`** 라서 처음에는 나와의 채팅에만 답을 보냅니다.
나와의 채팅으로 충분히 시험한 뒤 `false`로 바꾸고, 원하면 `native_send_rooms_only`에 허용할 방 chat_id만 넣으세요.

```powershell
py -3.12 kakaowin.py bot                                        # 터미널에서
powershell -ExecutionPolicy Bypass -File start_native.ps1       # 꺼지면 다시 켜는 감시 루프
```

로그온할 때 자동으로 켜려면 작업 스케줄러에 `start_native.ps1`을 숨김 창으로 등록합니다(선택).
절전·종료로 꺼진 동안 온 메시지는 다시 켜질 때 저장된 커서 뒤부터 이어서 처리합니다.

카톡에서 `!ping`, `!id`(이 방 chat_id)로 확인하고, 필요한 chat_id를 `config.json`에 넣습니다.

### 구조

```
 PC 카카오톡 (본인 계정 로그인)
   │  로컬 DB (%LOCALAPPDATA%\Kakao\KakaoTalk\users\…\*.edb, SQLCipher 암호화)
   ▼
 kakao_native/  ── 메모리에서 DB 키 찾기(읽기 전용) → 복호화(메모리에서 열기, 바뀐 방만) → 방·사람·메시지
   ▼
 iris_native.py (:3001, Iris 호환 HTTP)  ── 새 메시지 폴링 → 웹훅
   ▼                                         ▲ /reply (받은 메시지에 대한 답장만)
 bot.py (:5000)                              │
   1. 방 종류·호출어·등록 방·무시 목록 확인
   2. 코드 관문: 약속·돈·결정·비공개 주제 → LLM 없이 보류
   3. 프롬프트 = 답장 지침 + 공개 범위별 프로필 + 높임 + 기록
   4. claude -p → JSON(범주·위험 표시·확신도·초안)
   5. 위험 표시/확신<0.6 → 무전송 또는 짧은 미룸 + 할 일
   6. 출력 검사: 비공개 어휘·다른 상대 이름 → 보류, 높임 불일치 → 한 번 고쳐 쓰기/보류
   7. 쿨다운·시간당 상한·랜덤 지연 뒤 /reply
   ▼
 pc_kakao_send.py ── PC 카톡 창 찾기/열기 → 입력칸에 글 → 전송 버튼 → 카톡 DB로 전달 확인
```

### 설정 (`config.json`)

| 키 | 뜻 |
|---|---|
| `iris_url` | 봇이 붙을 셔임 주소. 기본 `http://127.0.0.1:3001` |
| `native_send_only_memo` | `true`면 나와의 채팅에만 보냄(시험 잠금, 예시 기본값 `true`) |
| `native_send_rooms_only` | 비어 있지 않으면 이 chat_id 방에만 보냄 |
| `wake_words`, `trigger_prefix` | 단톡·오픈채팅에서 봇을 부르는 말 |
| `auto_reply_room_types`, `dm_pipeline_room_types` | 호출어 없이 자동 답장할 방 종류(기본 1:1 갠톡) |
| `auto_reply_rooms` | 1:1이 아니어도 갠톡처럼 답할 단톡 chat_id |
| `dm_min_confidence`, `dm_debounce_sec`, `dm_max_reply_chars` | 보류 기준 확신도, 연달아 온 메시지 묶기, 답 길이 |
| `reply_delay_sec`, `room_cooldown_sec`, `max_replies_per_hour` | 지연과 상한 |
| `ontology_mode`, `dm_lookup` | 상대 카드, 내 카톡에서 근거 찾기 |
| `briefing_enabled` | 예약 브리핑(기본 꺼짐 — 봇은 먼저 보내지 않는다) |
| `heart_reaction` | 마무리 인사·감사·확인뿐인 짧은 갠톡 글에 글 대신 하트(기본 꺼짐) |
| `group_opinion` | 단톡에서 방 전체에 의견을 묻는 글을 부름으로 보고 답 여부 판단(기본 꺼짐) |
| `catchup_batch_gap_min`, `catchup_hard_max_age_h`, `catchup_owner_max_age_min` | 꺼져 있던 동안 쌓인 글 보충 범위: 방마다 마지막 묶음만, 이 시간을 넘긴 글은 건너뜀 |
| `calendar_mode` | 캘린더 연동(기본 꺼짐) |

갠톡 범주(`dm_categories.json`): 가족(자동응답 안 함)·친구·학교·교수·업무·외부·거래·서비스·기타·미분류.
처음 연락 온 방은 '미분류'로 자동 추가되고, 나와의 채팅에서 `!범주 <이름> <범주>`로 바꿉니다.

### 안전 설계

- **먼저 보내지 않는다**: 받은 메시지 id(trigger) 없는 전송은 코드가 거부합니다. 같은 메시지에 두 번 답하지 않습니다.
- **시험 잠금이 기본**: 예시 설정은 나와의 채팅에만 보냅니다. `config.json`을 못 읽으면 잠금으로 동작합니다.
- **코드가 강제하는 보류**: 약속·출결 확정, 돈·계좌, 개인정보, 결정, 분쟁, 봇에게 하는 지시, 'AI야?' 질문은 AI 판단과 무관하게 보류하고 주인의 `!할일`과 PC 알림에 남깁니다.
- **위조 문맥 차단**: 받은 글 속 '나: 콜' 같은 줄은 주인 말로 보지 않습니다. 화자는 user_id로만 정합니다.
- **AI냐고 물으면 거짓말하지 않는다**: '아니 사람이야' 같은 답은 코드가 막고 보류합니다.
- **받은 메시지는 데이터**: 메시지 속 지시는 따르지 않습니다. 갠톡 판정에는 웹 도구를 주지 않습니다.
- **주인 명령은 나와의 채팅에서만**, **채널(PlusChat)에는 보내지 않음**, **쓰던 초안은 덮어쓰지 않음**.
- **전달 확인**: 입력칸이 비워진 것만으로 성공으로 보지 않고, 카톡 DB에 내 글이 들어갔는지 확인합니다. 확인이 안 되면 다시 보내지 않습니다.

## AI 연동

- 답장 초안은 [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)의 `claude -p`로 만듭니다(설정 `claude_cmd`, `model`).
- 다른 AI 에이전트에서 이 도구를 쓸 때는 읽기 명령의 `--json` 출력을 쓰고, 보내기 전에는 반드시 사람에게 확인받도록 규칙을 두세요.
  예: "`kakaowin.py send`는 사용자가 보낼 글과 방을 정했을 때만 `--yes`로 실행한다."

## 동작 원리

- **읽기**: PC 카카오톡은 대화를 `users\<계정 해시>\chat_data\chatLogs_<chat_id>.edb` 등 SQLCipher 4 형식 파일로 저장합니다.
  `kakao_native/memkey.py`가 실행 중인 내 `KakaoTalk.exe` 메모리에서 키 후보를 모아 각 DB 첫 페이지의 HMAC으로 검증하고,
  `sqlcipher4.py`가 페이지를 복호화합니다(체크포인트 전 최근 글이 있는 `-wal`도 반영). 원본 DB는 읽기만 합니다.
- **셔임**: `iris_native.py`가 복호화한 내용을 Iris 스키마(`chat_logs`, `chat_rooms`, `db2.friends` …)의 인메모리 SQLite로 옮겨
  `bot.py`가 쓰는 `/query`·`/reply`·웹훅 인터페이스를 제공합니다.
- **보내기**: `pc_kakao_send.py`가 PC 카톡의 채팅창(없으면 검색으로 열기) → 입력칸(`RICHEDIT50W`) → `WM_SETTEXT` → 전송 버튼 클릭 순으로 보냅니다.
- **선택 모듈 `keystore_v2.py`**: 최신 PC 카톡의 키 저장소(`appstate.dat`)로 전 방 키를 한 번에 유도하는 모듈 자리가 있습니다.
  이 저장소에는 들어 있지 않습니다([라이선스·크레딧](#라이선스크레딧)). 없으면 메모리 키 방식(기본)으로 동작합니다.

## 알려진 제한

- **카톡이 최근에 연 방만 읽힙니다**(메모리 키 방식). 최신 PC 카톡은 키를 16진수 문자열로 두지 않아 raw 32바이트 후보를 훑으며,
  개발 PC 실측에서 채널을 뺀 방 42개 중 4개의 키가 한 번에 잡혔습니다(1회 약 130초). 새 메시지가 오면 카톡이 그 DB를 열기 때문에
  셔임이 2분마다(바뀐 방이 있을 때만) 다시 찾습니다. 오래 안 연 방·나와의 채팅은 그 방을 한 번 열고 `scan`을 실행하세요.
  키 저장소(v2) 모듈을 따로 갖고 있다면 전 방을 한 번에 열 수 있습니다(아래 '동작 원리').
- 카카오톡 업데이트로 DB 형식·창 구조가 바뀌면 깨질 수 있습니다. 확인한 창 구조는 2026년 하반기 PC 카카오톡 기준입니다.
- 스레드(오픈채팅 댓글) 답장은 `pc_kakao_thread.py`가 창 메시지만으로 답글 모드를 켠 뒤 보냅니다. 답글 모드 전환까지만 실측했고,
  실제 전송은 아직 확인하지 못했습니다. 좌표는 26.9.2 영어 UI 기준이며 클립보드를 잠깐 씁니다.
  버튼·메뉴 자리와 조작 방법은 [docs/kakaotalk-pc-manual.md](docs/kakaotalk-pc-manual.md)에 있습니다.
- 하트 반응(`pc_kakao_react.py`)은 갠톡 상대 글 1건에서 실제로 달리는 것까지 확인했습니다. 단톡·오픈채팅의 메뉴 높이는 스레드 답장용 값(282)을 그대로 쓰며 반응은 확인하지 못했습니다.
- 사진 보내기는 PC 창 경로로 하지 않습니다(받기만).
- 프롬프트는 한국어 전용, 시간은 KST(UTC+9) 고정, Windows 전용입니다.
- 온톨로지(상대 카드)는 주인의 실제 대화로 만들어지므로 처음에는 비어 있습니다. 말투 학습 기능은 없습니다(0.2.0에서 뺌).
- 다른 사람의 메시지를 외부 LLM(Claude)에 보냅니다. 상대의 개인정보에 미칠 영향을 고려하세요.

## 면책

- 이 프로젝트는 **카카오(Kakao Corp.)와 제휴·보증 관계가 없습니다.** "KakaoTalk/카카오톡"은 카카오의 상표입니다.
- **하는 것**: 내 PC의 내 카카오톡 로컬 DB를 읽기 전용으로 읽기, 내 PC 카톡 창에 글 넣기, 내 카톡 프로세스 메모리를 읽기 전용으로 읽어 내 DB 키 찾기.
- **하지 않는 것**: 카카오 서버·API 호출, LOCO 프로토콜 분석·재구현, 카카오톡 프로그램 수정·디컴파일, 인증 우회, 다른 사람 계정·다른 PC 대상 동작.
- **본인 계정·본인 데이터에만 쓰세요.** 개인 카카오톡 계정 자동화는 카카오 운영정책·이용약관에 어긋날 수 있고
  **계정 이용 제한(정지)** 위험이 있습니다. 그로 인한 결과(계정 제한 포함)에 대해 저자는 책임지지 않습니다.
- 기본값은 보수적입니다: 먼저 보내지 않음, 나와의 채팅만 보냄(시험 잠금), `send`는 `--yes` 없이는 보내지 않음.
  바쁜 단톡, 결제·예약 확정 용도로 쓰지 마세요. 상대에게 AI가 답한다는 사실을 알릴지는 사용자가 판단해야 합니다.

## 라이선스·크레딧

- 이 저장소의 코드는 **MIT** 라이선스입니다(`LICENSE`).
- [Iris](https://github.com/dolidolih/Iris) (GPL-3.0): 선택 경로에서 **별도 프로그램으로 실행해 HTTP로 통신**할 뿐, Iris 코드를 포함하거나 링크하지 않습니다.
  `iris_native.py`는 Iris의 HTTP 인터페이스 모양만 맞춘 독자 구현입니다.
- SQLCipher 형식은 공개 문서([SQLCipher 설계](https://www.zetetic.net/sqlcipher/design/), [SQLite 파일 형식](https://sqlite.org/fileformat.html))를 따라 직접 구현했습니다.
- 저장소 구성(README 순서, 명령 체계)은 맥용 [kakaocli](https://github.com/silver-flight-group/kakaocli) (MIT)를 참고했습니다. 코드·문장은 가져오지 않았습니다.
- **고맙습니다**: [getCurrentThread/kakaotalk-windows-forensics](https://github.com/getCurrentThread/kakaotalk-windows-forensics) (2026년 10월 공개).
  PC 카카오톡 키 저장소(v2) 구조를 밝힌 연구입니다. 제가 마지막으로 읽은 자료였고, 이분 덕분에 구현이 가능했습니다.
  다만 그 연구는 **오픈소스 배포를 금지하는 라이선스**를 달고 있어, 그 연구에 근거한 `keystore_v2.py`는 이 저장소에 넣지 않았습니다.
- 카카오톡·Claude Code는 이 저장소에 포함되지 않으며 각자의 약관을 따릅니다.

---

## English

**kakao-autoreply-win** is an unofficial, personal-use KakaoTalk auto-reply bot and CLI for **Windows**. It reads the
signed-in user's own local KakaoTalk PC databases (SQLCipher 4; keys are recovered read-only from the user's own running
`KakaoTalk.exe` and cached with Windows DPAPI), exposes them through an Iris-compatible local HTTP shim, lets `bot.py`
draft replies with the Claude Code CLI behind code-enforced hold rules (no unsolicited messages,
money/appointments/personal data/decisions are held for the owner), and sends by typing into the KakaoTalk PC window,
then confirms delivery in the local database. No Android emulator is required.
`kakaowin.py` offers `status`, `chats`, `messages`, `scan`, `send` (dry-run unless `--yes`; `--me` for self-chat), `serve` and `bot`.
Defaults are conservative: sending is locked to the self-chat until `native_send_only_memo` is set to `false`.
It does not call Kakao servers or reimplement the LOCO protocol. Not affiliated with Kakao Corp.; automating a personal
account may violate Kakao's terms and can get the account restricted — use at your own risk. MIT licensed.
