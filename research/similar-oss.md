<!-- 2026-11-08 Workflow 46 에이전트(관점 5 → 검증 40 → 종합) 결과. 수치는 검증 시점 GitHub 기준 -->
# OWNER 봇과 비슷한 오픈소스 조사 보고서

## 1. 결론

OWNER이 만든 조합 전체(개인 카톡 계정으로 본인 확인 없이 대신 답장 + 약속·돈·결정 보류를 코드가 강제 + 카톡·학교 사이트를 읽어 사실마다 출처·확신·공개 범위를 가진 '나 중심 온톨로지' + 상대 카드와 검색 근거로 답장)를 그대로 갖춘 오픈소스는 검증된 40개 중 없다. 큰 프로젝트들(OpenClaw·Hermes·NanoClaw·nanobot·KIRA)은 방향이 반대다. 주인이 자기 비서에게 명령하는 구조이고, 모르는 발신자는 기본 거부한다. 메모리 계층 프로젝트들(Graphiti·Mem0·Cognee·Letta·memU)은 답장 대행이 없다. 가장 가까운 3개는 다음과 같다. 첫째 WeClone. 채팅 기록으로 '생애 초상'(사실마다 출처·4단계 공개 범위)을 만들고 말투 클론을 메신저 봇으로 배포한다. 다만 LoRA 미세조정 기반이고 실시간 '에이전트 클론'은 미완이다. 둘째 PigeonAI. 확신도 미달·민감 메시지는 초안으로 보류하고 사람이 보내는 설계가 OWNER 봇과 가장 닮았다. 다만 스타 0, 하루치 커밋이고 확신도는 휴리스틱이다. 셋째 whatsapp-digital-twin. 말투 미세조정 + 인물 카드 RAG + 민감 주제 차단 + 실제 답장까지 갖췄다. 다만 커밋 3개, 라이선스 없음이다. 온톨로지 계층만 놓고 보면 Graphiti가 유일한 high 적합도다.

## 2. 비교표

| 이름 | 무엇 | 비슷한 부분 | 스타 | 라이선스 | 최근 활동 | 로컬 실행 | 적합도 |
|---|---|---|---|---|---|---|---|
| Graphiti (Zep) | 시간축 지식그래프 프레임워크, 관계마다 fact·valid_at·invalid_at·출처 에피소드 | 사실마다 출처·시간, 모순은 무효화로 이력 보존 | 32,635 | Apache-2.0 | 2026-11 | 가능 | high |
| WeClone | 채팅 기록→생애 초상+LoRA 말투 클론→Telegram·Discord 봇 배포 | 답장 대행+초상 메모리(출처·4단계 공개범위)+채팅 수집 | 18,370 | AGPL-3.0 | 2026-11 | 가능 | medium |
| Second Me | 개인 데이터로 Qwen2.5 로컬 학습, 계층적 기억 모델링, 롤플레이 | 개인 메모리 요약 계층, 상황별 페르소나 | 15,693 | Apache-2.0 | 2025-09 | 가능 | medium |
| Doppelganger | 채팅 내보내기(카카오톡 포함)→ShareGPT→LoRA 말투 학습 | 카카오톡 파서, 페르소나 인터뷰, PII 스캔 | 16 | MIT | 2026-11 | 가능 | medium |
| Enchanted Twin | 프라이버시 중심 데스크톱 디지털 트윈(Go+Electron), 채팅·메일 가져오기 | 사실 추출 스키마(Sensitivity·Importance·Source) | 92 | BSD-3-Clause | 2025-11 | 가능 | medium |
| Mem0 | user_id 단위 사실 추출·저장·검색 메모리 레이어 | 사실 추출→ADD/UPDATE/DELETE 루프 | 68,593 | Apache-2.0 | 2026-11 | 가능 | medium |
| Cognee | 벡터+그래프 결합 메모리 플랫폼, Drive·Gmail 커넥터 | 근거 원문 동반 recall, 충돌 검토 큐 | 33,128 | Apache-2.0 | 2026-11 | 가능 | medium |
| Letta (구 MemGPT) | 에이전트가 자기 편집하는 메모리 블록 플랫폼 | 블록 단위 상대 카드, 채널 어댑터 계약 | 25,383 | Apache-2.0 | 2026-11 | 가능 | medium |
| memU | Markdown 위키형 개인 메모리, sqlite 기본 | L0/L1/L2 3층 + source_ref 연쇄 무효화 | 14,605 | Apache-2.0 | 2026-11 | 가능 | medium |
| mcp-memory-service | MCP 로컬 영구 메모리 서버, SQLite+ONNX | confidence 붙은 belief, superseded 처리 | 2,095 | Apache-2.0 | 2026-11 | 가능 | medium |
| OpenClaw | 20개 이상 메신저를 한 에이전트에 붙이는 게이트웨이 | DM 정책 4모드, NO_REPLY, 메모리 승격 게이트 | 394,155 | MIT | 2026-11 | 가능 | medium |
| Hermes Agent | 다중 메신저 게이트웨이 범용 에이전트, 스킬 자동 생성 | Honcho 피어 모델, 메모리 하드 캡, FTS5 세션 검색 | 261,554 | MIT | 2026-11 | 가능 | medium |
| NanoClaw | Claude Agent SDK 기반 경량 메신저 에이전트, 컨테이너 격리 | 구조가 가장 닮음, OKF 메모리 포맷, 신뢰 모델 | 31,092 | MIT | 2026-11 | 가능 | medium |
| nanobot (HKUDS) | 개인 에이전트, Dream으로 장기 메모리 압축 | 저널→Dream→git 메모리, pairing.json | 49,909 | MIT | 2026-11 | 가능 | medium |
| CowAgent (구 chatgpt-on-wechat) | 중국권 개인 메신저 비서 프레임워크 | 개인 계정 자동 답장, 3단 메모리+Deep Dream | 47,834 | MIT | 2026-11 | 가능 | medium |
| Agent Messenger | 사용자 계정으로 메신저 읽고 쓰는 CLI·SDK, 카카오톡 LOCO 포함 | Iris 대체 후보(PC 카톡 자격증명) | 255 | MIT | 2026-11 | 가능 | medium |
| HPI | 개인 데이터 내보내기를 my.* 파이썬 모듈로 통일 | 수집·정규화 레이어, 안드로이드 DB 직접 읽기 | 1,646 | MIT | 2026-11 | 가능 | medium |
| Timelinize | 여러 소스를 한 타임라인(SQLite)으로, 사람 엔티티 병합 | 엔티티-속성 분리, 아이템 단위 출처 추적 | 3,698 | AGPL-3.0 | 2026-05 | 가능 | medium |
| browser-use | LLM이 브라우저 조작, 로그인 프로필 재사용 | 학교 사이트 수집 폴백 | 120,300 | MIT | 2026-10 | 가능 | medium |
| Iris | 루팅 안드로이드 카톡 DB 감시→웹훅→답장 브로커 | OWNER 봇이 쓰는 부품, v0.33 조회 API | 88 | MIT + GPL-3.0 파일 병존 | 2026-11 | 가능 | medium |
| KIRA (krafton-ai) | 크래프톤 개인 AI 비서, 파일 기반 메모리, 채널 어댑터 | memories 폴더 레이아웃, 보류 테이블 | 934 | Apache-2.0 | 2026-05 | 가능 | medium |
| synaptic-memory | 한국 팀 지식그래프 MCP 서버, Kiwi 한국어 FTS | 카톡 DB CDC 동기화, 한국어 검색 | 33 | Apache-2.0(README 기준) | 2026-07 | 가능 | medium |
| PigeonAI | Telegram 유저봇, 내 말투 분석, 확신도 기반 자동/초안 | 답장 대행+보류+초안 승인 설계 | 0 | README MIT, 파일 없음 | 2026-05 | 가능 | low |
| Fake Me | Telegram Business 메시지에 채팅별 지시문으로 대신 답장 | 지시문 세트↔상대 매핑 | 7 | MIT | 2026-07 | 가능 | low |
| whatsapp-digital-twin | WhatsApp 말투 QLoRA + 인물 카드 RAG + 안전필터 + 실제 답장 | 답장 대행+상대 카드+민감 차단 | 0 | 없음 | 2026-11 | 불가 | low |
| whatsapp-ai-clone | WhatsApp 채팅으로 Llama3 미세조정, CLI 대화 | 개인 데이터로 나 학습 | 426 | 없음 | 2026-05 | 가능 | low |
| WhatsApp-Llama | 2023년 Llama-2 QLoRA 말투 클론, 튜링 테스트 | 초기 사례, 평가 방식 | 378 | Llama 2 Community | 2023-09 | 가능 | low |
| Khoj | 셀프호스팅 개인 세컨드 브레인, 문서 색인 | 문서 RAG, 색인 파이프라인 | 38,052 | AGPL-3.0 | 2026-08 | 가능 | low |
| Memori | 엔티티·프로세스·세션 3단계 구조화 메모리 | 트리플 스키마, mention_count | 17,461 | Apache-2.0(추출 엔진 비공개) | 2026-10 | 불가 | low |
| screenpipe | 화면·오디오 24시간 기록, OCR, MCP | pipes 권한 선언, FTS5+MCP 래퍼 | 22,263 | 상업 라이선스(개인 무료) | 2026-11 | 가능 | low |
| Pensieve (구 memos) | 5초 간격 스크린샷 OCR 색인 | 검색 SKILL.md, 수집 정책 | 1,403 | Apache-2.0 | 2026-07 | 가능 | low |
| OpenRecall | 스크린샷→OCR→의미 검색 최소 구현 | 암호화 볼륨 저장, 최소 의미 검색 | 2,970 | AGPL-3.0 | 2025-09 | 가능 | low |
| Promnesia | 브라우저 확장 '이 페이지 어디서 봤나' | Visit/Loc 근거 레코드 모델 | 1,913 | MIT | 2026-10 | 가능 | low |
| Onyx (구 Danswer) | 엔터프라이즈 커넥터 검색·챗 플랫폼 | Load/Poll/Slim 커넥터 패턴 | 33,280 | MIT(CE) + Enterprise | 2026-11 | 가능 | low |
| LlamaIndex readers | 100개 이상 데이터 로더 | WhatsApp 리더 메타 규약 | 52,842 | MIT | 2026-11 | 가능 | low |
| irispy-client | Iris용 Python SDK | 앞뒤 메시지 컨텍스트 함수 | 14 | 없음 | 2026-02 | 가능 | low |
| kmsg | macOS 카톡 접근성 CLI·MCP | 전송 확인 토큰, 방 ID 레지스트리 | 283 | MIT | 2026-09 | 불가 | low |
| kakaocli | macOS 카톡 SQLCipher 읽기 CLI, harvest | 에이전트용 CLI 표면, NDJSON | 185 | MIT | 2026-03 | 불가 | low |
| kakaotalk-mcp | Windows PC 카톡 Win32 제어 MCP | MCP 도구 명명 체계 | 7 | MIT | 2026-06 | 가능 | low |
| openclaw-kakao | OpenClaw에 카카오 i 오픈빌더 채널 연결 | dmPolicy, 문장 단위 분할 | 0 | MIT(README 기준, 파일 미감지) | 2026-02 | 가능 | low |

## 3. 가져다 쓸 것

### 답장 품질

- 내 과거 답장을 few-shot으로 넣기. whatsapp-ai-clone과 WhatsApp-Llama의 '맥락(최근 몇 줄)→내 실제 답장' 쌍 추출 흐름을 미세조정 없이 Iris DB에서 상대별로 뽑아 Claude 프롬프트에 끼운다. 보수적 샘플링(temperature 0.2 수준)도 참고. 출처: kinggongzilla/whatsapp-ai-clone, Ads97/WhatsApp-Llama.
- 통계식 말투 프로필. PigeonAI style_analyzer.py의 평균 길이·이모지·구두점 통계를 한 줄 요약으로 프롬프트에 넣는 방식. 한국어는 어미·호칭 통계로 바꿔야 한다. 출처: naruto11eth/PigeonAI.
- 주인 수정 기록을 학습 근거로. PigeonAI feedback_events 4종(approved/edited/rejected/manual)에 원문 초안과 최종문을 저장하고, 수정 패턴을 'LEARNED FROM YOUR CORRECTIONS' 블록으로 주입. 출처: naruto11eth/PigeonAI.
- 사실·기억·말투 분리 추출. WeClone의 weclone/prompts 단계별 프롬프트(clean_data→deep_insights→distill_memory/distill_style→profile_summary). 중국어 기준이라 한국어 번안 필요. 출처: xming521/WeClone.
- 상황별 페르소나 키. Second Me의 role_id(상황별 페르소나 선택) 개념과 Fake Me의 instruction_sets↔chats 두 층 매핑. 존댓말/반말·범주별 설정을 카드와 분리하면 관리가 단순해진다. 출처: mindverse/Second-Me, derogab/fake-me.
- 페르소나 카드 인터뷰와 블라인드 평가. Doppelganger의 interview(생활·사람·의견·문자 습관 문답)와 eval blind, WhatsApp-Llama의 친구 대상 2지선다 식별률 테스트. 출처: NotYuSheng/Doppelganger, Ads97/WhatsApp-Llama.
- 검색 근거 포맷. memU progressive_retrieve의 자족적 hit(path·content·track·score·created_at) compact JSON, Cognee의 근거 원문 동반 recall. 출처: NevaMind-AI/memU, topoteretes/cognee.
- 긴 답 분할. openclaw-kakao의 textChunkLimit(기본 400자)+chunkMode(sentence|newline|length). 출처: burlesquer/openclaw-kakao.

### 개인 메모리(온톨로지)

- 사실 스키마의 뼈대. Graphiti EntityEdge의 fact·episodes(출처 uuid 목록)·valid_at/invalid_at·created_at/expired_at 4중 시간축과 '모순은 삭제 대신 무효화' 규칙. 카톡 메시지 1건=에피소드 1건으로 매핑. 확신·공개범위는 attributes나 커스텀 edge 타입에 추가, group_id는 '나/상대/학교' 파티션. FalkorDB+Ollama로 로컬 구성. 출처: getzep/graphiti.
- 필드 정의 참고. Enchanted Twin MemoryFact의 Subject/Attribute/Value/Category/Sensitivity(high·medium·low)/Importance/Source + '확신 7/10 이상만 저장' 게이트, 20개 주제 consolidation, 고정 6개 질의로 성격 프로필 생성. 출처: EternisAI/enchanted-twin.
- 공개 범위 등급 체계. WeClone 초상의 4단계(public/normal/private/sensitive)+도메인 분류, '도메인×등급' 단위 MCP 노출 제한. 상대 카드의 등급 틀로 바로 쓸 수 있다. 출처: xming521/WeClone.
- 확신 게이트와 superseded. mcp-memory-service의 신뢰도 띠(0.60 이하 related 강등, 0.60~0.85 needs_review, 0.85 이상 확정)와 include_superseded/as_of 시점 조회. 출처: doobidoo/mcp-memory-service.
- 중복·모순 정리 루프. Mem0의 사실 추출→기존 메모리 대조→ADD/UPDATE/DELETE/NONE 판정. 출처: mem0ai/mem0.
- 3층 메모리와 연쇄 무효화. memU의 L0 원본→L1 범주별 md→L2 세부 항목, 모든 항목이 source_ref를 가져 원본 삭제 시 파생 사실 연쇄 삭제. MemoryService가 LLM을 부르지 않고 판단을 호스트 에이전트에 맡기는 분업은 Claude CLI 구조와 맞는다. 출처: NevaMind-AI/memU.
- 파일 포맷. NanoClaw OKF(1개념=1파일, YAML frontmatter)에 source·confidence·disclosure 필드 추가. nanobot의 history.jsonl→Dream→git 커밋(/dream-log·/dream-restore)로 감사·롤백. CowAgent의 대화→일일 메모리→MEMORY.md 3단과 야간 Deep Dream. 출처: nanocoai/nanoclaw, HKUDS/nanobot, zhayujie/CowAgent.
- 상대 카드 블록. Letta 메모리 블록(label/value/limit/read_only)을 상대별 블록으로, 코드가 공개범위 필터를 거친 값만 주입. KIRA의 memories/{channels,projects,users,decisions}/ + index.md 레이아웃. 출처: letta-ai/letta, krafton-ai/KIRA.
- 상대 식별 계층. Timelinize의 entities↔attributes↔entity_attributes(data_source_id 붙은 속성=그 소스에서의 신원)로 카톡ID·Moodle 계정을 한 사람으로 묶고 병합. items의 data_source_id·job_id·original_id·relations(답장). 출처: timelinize/timelinize.
- 피어 모델과 메모리 규율. Hermes Honcho 플러그인의 피어별 observations·conclusions·peer card 후행 추론(self-host 필수), MEMORY.md 2,200자·USER.md 1,375자 하드 캡, '항상 알 사실(메모리)'과 '지난주 얘기(FTS5 원문 검색)' 분리. 출처: NousResearch/hermes-agent.
- 확신 근거로 언급 횟수. Memori의 memori_entity_fact_mention(mention_count·갱신 시각). 스키마만 참고. 출처: MemoriLabs/Memori.
- 근거 레코드. Promnesia Visit/Loc의 url·dt·src·locator(원본 점프 링크)·context 스니펫. 출처: karlicoss/promnesia.
- 한국어 검색. synaptic-memory의 Kiwi 형태소 BM25 FTS와 DbIngester(updated_at 워터마크·행 해시 증분). LLM 호출 없이 인덱싱. 출처: PlateerLab/synaptic-memory.

### 데이터 수집

- Iris 업그레이드가 가장 직접적. v0.33의 /rooms, /chat/{roomId}/messages(커서 페이지네이션), /search(SQLCipher 전문검색), /chat/{logId}/deleted, /reactions, /user/name 조회 API와 /ws WebSocket 구독. 단 v0.34가 암호화 DB 쿼리 실패 데드락을 고쳤으니 v0.34로. /process-status의 sqlcipher 상태 먼저 확인. 출처: dolidolih/Iris.
- 과거 카톡 백업 파서. Doppelganger의 kakaotalk 소스(Android·iOS·Windows PC·Mac 4 레이아웃 정규식, 1:1 방 '나' 자동 식별, 시스템 공지·자리표시자 제거, 멀티라인 병합, 시간 간격 묶기). 출처: NotYuSheng/Doppelganger.
- 수집 모듈 규약. HPI의 my.<source>(설정에서 경로→파싱→dataclass 이터레이터→cachew 캐시), my/core/sqlite.py(DB 스냅샷 복사 후 읽기 전용), my/core/denylist.py, 다중 메신저 공통 Message/Sender/Thread 스키마. 출처: karlicoss/HPI.
- 증분 커넥터 구조. Onyx의 LoadConnector/PollConnector(시간창 증분)/SlimConnector(삭제 감지) 분리와 Refresh·Prune 주기 설정. Khoj의 소스 어댑터→공통 엔트리→해시 기반 증분 재색인. 출처: onyx-dot-app/onyx, khoj-ai/khoj.
- 학교 사이트 폴백. browser-use Agent에 봇 전용 user_data_dir과 allowed_domains를 주고 셀렉터가 깨졌을 때만 pydantic output_model로 구조화 추출. Playwright 기반이 아니라 CDP 직접 사용이므로 별도 세션. 출처: browser-use/browser-use.
- Iris 대체 검토. Agent Messenger의 agent-kakaotalk(LOCO 서브디바이스 로그인, Windows login_list.dat 자격증명 추출, 실시간 리스너). 에뮬레이터 없이 가능하나 계정 제재 위험 동일 이상. 출처: agent-messenger/agent-messenger.
- 수집 운영 규칙. kakaocli의 unread 방 건너뛰기, 메시지당 NDJSON 한 줄(chatId, author, message, sentAt), send --dry-run. 출처: silver-flight-group/kakaocli.
- 출처 스탬프. Cognee처럼 문서 external_metadata를 모든 청크에 찍기. 출처: topoteretes/cognee.
- 검색 근거를 도구로 노출. screenpipe의 SQLite FTS5+localhost REST+얇은 MCP 래퍼, Pensieve의 SKILL.md(검색 API·context 엔드포인트·넓게→좁히기 지침). 출처: screenpipe/screenpipe, arkohut/pensieve.
- 메시지 리더 메타 규약. llama-index-readers-whatsapp의 메시지 단위 Document(text + 보낸이·시각·방). 출처: run-llama/llama_index.

### 사람 승인(보류·게이트)

- 자동 발송/초안 분기. PigeonAI의 임계값 이상 자동 발송, 미만은 초안 저장 후 send <id>, 연락처별 끄기, 분당 레이트리밋. 구조만 참고, 확신도 계산은 직접 설계. 출처: naruto11eth/PigeonAI.
- 전송 전 확인 토큰. kmsg의 confirm=true→CONFIRMATION_REQUIRED 반환, --dry-run, 가드레일 목록(내 메시지 무시·방 allowlist·쿨다운·루프 방지·발신 로그), 방 이름 대신 합성 chat_id 레지스트리, watch 시작 시 기준선 잡고 과거 메시지 재생 금지. 출처: channprj/kmsg.
- 코드 레벨 게이팅 정책. OpenClaw의 DM 4모드(pairing/allowlist/open/disabled), toolsBySender(id 키), '답 안 함'을 NO_REPLY 명시 토큰으로. 출처: openclaw/openclaw.
- 민감 키워드 보류 분기. whatsapp-digital-twin처럼 민감 키워드면 LLM 호출 없이 고정 문장 1개만 보내고 메모리에 저장 안 함. 단순 substring은 오탐이 있으니 단어 경계·정규식으로. 출처: blackmangoo/whatsapp-digital-twin.
- 보류 큐 영속화. KIRA-Slack의 SQLite '답 기다리는 질문·확인' 테이블. Cognee의 conflict review(상충 사실을 보류 큐에 넣고 사용자가 확정). 출처: krafton-ai/KIRA, topoteretes/cognee.
- 승인 저장 형식. nanobot pairing.json(8자리 코드·TTL 600초·채널별 approved/pending)과 temporary chat 메모리 제외 플래그. NanoClaw의 user_roles+unknown_sender_policy, 자격증명을 봇 밖 게이트웨이가 주입. Letta accounts.json의 DM 정책. 출처: HKUDS/nanobot, nanocoai/nanoclaw, letta-ai/letta.
- 승인 로직 모듈 분리. Hermes의 approval_detection/floors/smart/provenance 분리 구조. 출처: NousResearch/hermes-agent.
- 메모리 승격 게이트. OpenClaw Dreaming의 untrusted 출처 후보 승격 제외, 자동 컨텍스트에서 untrusted 파일 제외. 출처: openclaw/openclaw.

## 4. 주의

- AGPL-3.0: WeClone, Khoj(WhatsApp 봇 flint는 GPL-3.0), OpenRecall, Timelinize. 개인용 비배포 봇에는 실질 영향 없다. 코드를 포함해 배포하면 소스 공개 의무가 생긴다. 아이디어·스키마만 참고가 안전하다.
- 라이선스 혼재·불명: Iris는 LICENSE-MIT와 LICENSE-GPLv3.0이 병존하고 GitHub은 GPL-3.0으로 표기한다. screenpipe는 상업 라이선스(개인 무료, 수정본 소유권이 라이선서에 귀속). memU·Memori·synaptic-memory는 GitHub API가 Other/NOASSERTION이나 실질은 Apache-2.0.
- 라이선스 없음(코드 복사 불가): whatsapp-digital-twin, whatsapp-ai-clone, irispy-client. PigeonAI는 README에만 MIT, LICENSE 파일 없음. openclaw-kakao는 API license null.
- 클라우드 의존: Memori는 사실 추출이 api.memorilabs.ai로 대화 전문을 보낸다. Khoj WhatsApp은 Khoj Cloud 계정 필수. Hermes Honcho는 기본 클라우드(self-host 필요). whatsapp-digital-twin은 Supabase·Kaggle·WhatsApp API 의존. WeClone 기본 흐름은 클라우드 LLM. memU 임베딩은 API 키 필요.
- Claude CLI 구독으로 못 붙임: Mem0, Graphiti, Cognee, Letta, nanobot, CowAgent, KIRA, browser-use는 Anthropic API 키 또는 Ollama 전제. PigeonAI의 claude_oauth.py는 Claude Code 클라이언트 ID를 빌려 쓰는 방식이라 약관상 회색지대, 가져다 쓰지 말 것.
- 유지 중단·둔화: Second Me(실질 커밋 2025-05), Enchanted Twin(2025-11 이후 없음), WhatsApp-Llama(2023), whatsapp-ai-clone(코드 2024-06 이후 README만), Fake Me(dependabot만), OpenRecall(기능 커밋 2025-04까지), irispy-client(2026-02), kakaocli(2026-03 이후 정체), Khoj flint(아카이브), KIRA-Slack(legacy 명시), Letta V1 서버(archive 브랜치), PigeonAI(커밋 전부 하루치). browser-use는 'final Python runtime release' 명시로 향후 방향 불명.
- 과장된 주장: PigeonAI 확신도는 길이·이모지 일치 휴리스틱(최대 0.95), 민감 필터는 영어 14단어 매칭. nanobot '4,000줄 코어'는 실제 약 14.4만 줄. Second Me·Hermes·synaptic-memory·Timelinize는 '온톨로지'가 아니다(확신·공개범위 필드 없음). mcp-memory-service의 causes/fixes/contradicts 그래프는 기본 꺼짐, 모순 감지는 키워드 휴리스틱. Onyx 권한 동기화는 유료 플랜 전용. Memori 'BYODB=로컬'은 DB만 로컬. browser-use는 Playwright 기반이 아니다. screenpipe·Pensieve·OpenRecall 화면 OCR은 Iris DB 직접 읽기보다 하위 수집 경로.
- 방향이 반대: OpenClaw·Hermes·NanoClaw·nanobot·KIRA·CowAgent는 주인이 자기 비서에게 명령하는 모델. 모르는 발신자는 기본 거부(pairing). 상대별 공개 범위 카드와 약속·돈·결정 보류 가드는 어디에도 없다.
- 카카오톡 채널 없음: Agent Messenger·Doppelganger·Iris 계열을 빼면 전부 없다. 카카오 비공식 클라이언트(Agent Messenger·kmsg·kakaocli)는 계정 제재 위험을 저장소가 직접 경고하거나 Iris와 동일하다. openclaw-kakao는 공식 채널 봇 경로라 개인 계정 대행에 못 쓴다.
- 환경 충돌: NanoClaw는 Windows에서 WSL2+Docker 필수라 LDPlayer와 가상화 충돌 가능. Onyx Standard·Cognee·Khoj는 Postgres 등 무거운 스택. kmsg·kakaocli는 macOS 전용.
- 저장소 이동: WhatsApp-Llama는 Ads97/WhatsApp-Llama로, NanoClaw는 nanocoai/nanoclaw로, chatgpt-on-wechat은 zhayujie/CowAgent로 이동(옛 URL 리다이렉트).
- 미검증 외부 주장: 'Zep Community Edition 2025년 유지 종료'는 README에서 확인되지 않음. Khoj가 홍보하는 Pipali는 미검증. irispy-client 설명의 node-iris는 404.

## 5. 출처 URL

- https://github.com/xming521/WeClone
- https://github.com/mindverse/Second-Me
- https://github.com/NotYuSheng/Doppelganger
- https://github.com/naruto11eth/PigeonAI
- https://github.com/derogab/fake-me
- https://github.com/blackmangoo/whatsapp-digital-twin
- https://github.com/kinggongzilla/whatsapp-ai-clone
- https://github.com/EternisAI/enchanted-twin
- https://github.com/Ads-cmu/WhatsApp-Llama (이동: https://github.com/Ads97/WhatsApp-Llama)
- https://github.com/mem0ai/mem0
- https://github.com/getzep/graphiti
- https://github.com/getzep/graphiti/blob/main/graphiti_core/edges.py
- https://github.com/topoteretes/cognee
- https://github.com/letta-ai/letta
- https://docs.letta.com/concepts/memgpt
- https://github.com/khoj-ai/khoj
- https://github.com/NevaMind-AI/memU
- https://github.com/doobidoo/mcp-memory-service
- https://github.com/MemoriLabs/Memori
- https://github.com/GibsonAI/memori
- https://github.com/openclaw/openclaw
- https://docs.openclaw.ai/gateway/security
- https://github.com/NousResearch/hermes-agent
- https://github.com/qwibitai/nanoclaw (이동: nanocoai/nanoclaw)
- https://github.com/HKUDS/nanobot
- https://github.com/zhayujie/chatgpt-on-wechat (개명: zhayujie/CowAgent)
- https://github.com/agent-messenger/agent-messenger
- https://github.com/screenpipe/screenpipe
- https://github.com/arkohut/pensieve
- https://github.com/openrecall/openrecall
- https://github.com/karlicoss/HPI
- https://github.com/karlicoss/promnesia
- https://github.com/timelinize/timelinize
- https://github.com/onyx-dot-app/onyx
- https://github.com/browser-use/browser-use
- https://github.com/run-llama/llama_index/tree/main/llama-index-integrations/readers
- https://pypi.org/project/llama-index-readers-google
- https://github.com/dolidolih/Iris
- https://github.com/dolidolih/irispy-client
- https://github.com/channprj/kmsg
- https://github.com/silver-flight-group/kakaocli
- https://github.com/kronenz/kakaotalk-mcp
- https://github.com/krafton-ai/KIRA
- https://kira.krafton-ai.com
- https://github.com/PlateerLab/synaptic-memory
- https://github.com/burlesquer/openclaw-kakao
- https://www.pkgstats.com/pkg:openclaw-kakao