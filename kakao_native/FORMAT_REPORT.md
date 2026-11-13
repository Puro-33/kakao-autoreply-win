# 포맷 분석 및 검증 결과

2026-11-12. 이 문서는 구현 결과와 실제 파일에서 확인하지 못한 사항을 구분한다.

**코드·합성 시험은 검증했고, 실제 Windows 카카오톡 포맷은 미확정이다.**

## 실제 파일에 대한 관측 범위

지정된 chat_data 폴더를 읽기 전용으로 열람하려 했으나 Windows에서
UnauthorizedAccessException / DirUnauthorizedAccessError(Access denied)가 발생했다.
원본 EDB를 연 개수는 0개다. 이 결과는 파일이 없다는 뜻이 아니다.
권한 우회, 원본 복사·변경·삭제, 실제 키 사용, 메모리 스캔은 수행하지 않았다.

| 항목 | 실제 측정 결과 |
|---|---|
| chatLogs_*.edb 개수 106개 | 사용자 제공 정보이며 이번 실행에서 확인하지 못함 |
| 모든 파일 크기가 4096의 배수 | 사용자 제공 정보이며 이번 실행에서 확인하지 못함 |
| 첫 16바이트의 고유값 수·최대 동일 그룹 | 측정 불가 |
| 마지막 16/80바이트 및 -80:-64의 고유값·엔트로피 | 측정 불가 |
| 파일 크기의 최솟값·최댓값·GCD | 측정 불가 |
| 실제 page_size, reserve, salt/HMAC 유무, AES 키 길이 | 미확정 |

4096의 배수라는 사실을 받아들여도 512·1024·2048 페이지 가설을 배제하지 못한다.
서로 다른 첫 16바이트는 파일별 랜덤 salt와 CBC 암호문 모두에 부합한다.
서로 같은 첫 16바이트도 그 영역이 salt라는 증거가 되지 않는다.
암호문, IV, 인증값 모두 무작위처럼 보일 수 있어 엔트로피만으로 경계를 확정하지 않는다.
16바이트 표본의 경험적 바이트 엔트로피 최댓값은 log2(16)=4이므로
단일 짧은 표본을 8 bits/byte와 비교하여 salt 여부를 판정하지 않는다.

## 지원하는 두 기본 가설의 산술

아래 수치는 원본 실측이 아니라 코드가 지원하는 레이아웃 정의다.

| 항목 | sqlcipher4 | device-aes128 |
|---|---:|---:|
| 기본 page_size | 4096 | 4096 |
| AES 키 길이 | 256bit / 32B | 128bit / 16B |
| page1 평문 salt 공간 | 16B | 없음 |
| reserve | IV16 + HMAC64 = 80B | IV16 = 16B |
| page1 암호문 길이 | 4096−16−80 = 4000B | 4096−16 = 4080B |
| 이후 페이지 암호문 길이 | 4016B | 4080B |
| AES 블록 수(page1/이후) | 250 / 251 | 255 / 255 |
| SQLite magic 처리 | salt 자리에 16B 헤더 복원 | 실제 복호화된 magic 확인 |
| 키 검사 | page1 HMAC-SHA512 | 복호화한 SQLite 헤더 필드 |
| 전체 파일 검증 | 페이지별 HMAC + SQLite integrity_check | SQLite integrity_check; 암호학적 인증 없음 |

두 레이아웃 모두 AES 16B 정렬을 만족하므로 산술만으로 선택할 수 없다.
복호화 후 SQLite header offset 16..17의 page_size, offset 20의 reserve,
18..19의 버전, 21..23의 payload fraction 등을 확인해야 한다.
reserve는 평문 SQLite에서도 남겨 두고 해당 공간만 0으로 채운다.
512B 페이지에서 reserve80은 사용 가능 공간이 432B여서 SQLite 최소 480B를
충족하지 못한다. 이 조합은 거부하고, reserve16의 512B 페이지는 지원한다.

## 변경 사항

- sqlcipher4.py: CipherParams/SqlCipher4Params에 page_size, aes_key_bits,
  use_hmac, use_salt, reserve를 제공한다. 128/256 및 salt/HMAC 조합을
  명시적으로 지정할 수 있다. HMAC은 SHA512, 페이지 번호는 LE로 한정한다.
  비활성화는 use_* 플래그로 지정하며 salt_size/hmac_size는 알고리즘의
  기본 크기 16/64로 유지한다.
- SQLCipher HMAC 유도 키 길이를 64B에서 AES 키 길이인 32B로 수정했다.
  SHA512 인증값의 길이 64B와 구별한다. 예전의 잘못된 64B KDF 출력으로
  만든 합성 파일과의 암묵적 호환은 제공하지 않는다.
- detect_format(first_page, file_size): 상태, 후보 파라미터, 가능한 페이지
  크기, 근거를 담은 FormatDetection을 반환한다. 암호문만 있을 때는
  ambiguous이며 확정값을 꾸며내지 않는다. 최대 65536B를 제공하면 모든
  지원 페이지 크기를 키로 시도할 수 있다.
- matching_formats: 실제 키로 후보를 검사하고 복호화된 헤더까지 확인한다.
  자동 탐색은 두 프리셋의 페이지 크기 변형에 한정한다. 다른 조합은
  CipherParams를 직접 지정한다.
- format_analysis.py: 각 파일의 첫/중간/마지막 가정상 페이지를 읽고,
  파일 크기 분포·GCD·각 페이지 크기의 배수 개수, 첫16 및 꼬리 영역의
  고유값·동일 그룹·0바이트·합산 엔트로피를 출력한다. 원문 바이트·파일명은
  출력하지 않는다. 원본 파일의 디스크 복사본을 만들지 않는다.
- dbreader.py: verify_database_file/verify_database_bytes를 추가했다.
  rb로 읽어 RAM에서 복호화하고 sqlite3.deserialize + integrity_check를
  수행한다. 평문 임시 파일을 생성하지 않는다. 본문이나 키를 반환·출력하지 않는다.
- cli.py: --analyze-dir 및 --verify-key --db --format auto를 추가했다.
  실제 키는 getpass로 숨겨 입력하고, 입력을 숨길 수 없는 환경에서는 거부한다.
  이 두 경로는 memkey를 import하지도 실행하지도 않는다.
- memkey.py는 수정·import·실행하지 않았다. 기존 스캐너 호출에 선택된
  파라미터와 page_size를 전달하고 동일 파라미터로 DB를 읽도록 맞췄다.
  스캐너의 32B 후보 생성 제한은 유지하므로 AES128 스캔은 CLI에서 거부한다.
- 기존 임시 파일 기반 read_recent와 기본 스캔 실행 경로는 그대로 남아 있다.
  이번 요청의 복사·스캔 금지 범위에서는 아래 두 명령만 사용한다.

memkey.py 변경 전후 SHA256:
580d7bed93ae2619b42ba1f40b05231ad350d6f451de5fe0803778f0060aaf26

## 검증

독립적인 합성 암호화기로 실제 읽을 수 있는 두 페이지 SQLite DB를 암호화했다.
양쪽 프리셋, 유효한 512/1024/4096/8192/65536 페이지 조합, AES128/256,
salt/HMAC 조합, 추가 reserve, 잘못된 키·페이지 길이·손상·페이지 순서,
헤더 검증, WAL 헤더의 RAM 내 처리, 읽기 전용 파일 접근, CLI의 memkey
접근 방지를 검사했다. 실제 키나 실제 카카오톡 DB를 사용하지 않았다.

최종 대상 시험: 새 시험 50개 + 기존 코덱 회귀 시험 3개 = **53 passed**.
실행 환경: 기존 Anaconda Python 3.13.9, cryptography 46.0.3,
pytest 8.4.2, SQLite 3.51.0. 설치하지 않았다.

작업 폴더에서 재실행:
```powershell
python -m pytest tests/test_formats.py ../tests/test_kakao_native.py::test_codec_roundtrip_and_verify ../tests/test_kakao_native.py::test_decrypt_wrong_key_hmac_raises ../tests/test_kakao_native.py::test_decrypt_rejects_bad_size -q -p no:cacheprovider --ignore=../backup-20261109-prefix
```

상위 전체 테스트를 실행했다는 뜻은 아니다. 기존 memkey PID 검사와
백업 디렉터리의 수집은 실행하지 않았다. 상위 test_kakao_native.py에는
작업 중 별도로 변경된 API를 사용하는 시험과 HMAC 유도 키 64B 가정,
SQLite magic 뒤를 무작위로 채우는 시험이 남아 있다. 그 파일은 쓰기 허용
범위 밖이므로 수정하지 않았다. 새 시험은 실제 SQLite 헤더와 독립 KDF를 사용한다.
별도의 SQLCipher 실행 바이너리와 상호운용 검증은 수행하지 않았다.

## 사용자 실행

원본 폴더를 읽을 수 있는 본인 PowerShell에서 다음을 실행한다.
이 세션에서 py 런처는 없었고 python은 기존 Anaconda 실행 파일로 확인했다.

```powershell
Set-Location '<저장소 폴더>'
$chatData = Join-Path $env:LOCALAPPDATA 'Kakao/KakaoTalk/users/<계정 해시>/chat_data'
python -m kakao_native.cli --analyze-dir $chatData
```

이 명령의 JSON이 있어야 실제 파일별 비교 수치를 보고할 수 있다.
분석은 4096을 샘플링 가설로 사용하며 원문을 출력하거나 저장하지 않는다.

이미 확보한 본인 raw key로 한 파일을 검사할 때:
```powershell
python -m kakao_native.cli --verify-key --db (Join-Path $chatData 'chatLogs_실제파일명.edb') --format auto
```

실제 파일명을 넣고 숨김 프롬프트에 AES128은 32자리 hex, AES256은 64자리
hex를 입력한다. 키 획득이나 메모리 스캔은 이 명령에 포함되지 않는다.
특정 가설만 검사하려면 --format device-aes128 또는 --format sqlcipher4,
필요시 --page-size 4096을 추가한다.

성공 기준은 단일 키·헤더 일치, 해당 포맷으로 전체 페이지 복호화,
integrity_check: ok다. HMAC 없는 경우 인증은 보장하지 않는다.
WAL은 읽거나 재생하지 않으므로 main DB의 구조만 검사하며 최신 대화 반영을
보장하지 않는다. WAL 모드 헤더는 RAM에서만 rollback 모드로 정규화한다.
원본이 읽는 중 바뀌면 검증을 중단한다.

## 근거 문서

- [SQLCipher 설계](https://www.zetetic.net/sqlcipher/design/): 기본 salt·IV·HMAC 설계.
- [SQLCipher 공식 구현](https://raw.githubusercontent.com/sqlcipher/sqlcipher/master/src/sqlcipher.c):
  sqlcipher_cipher_ctx_key_derive의 HMAC KDF 출력 길이는 ctx->key_sz.
- [SQLite 파일 형식](https://sqlite.org/fileformat.html):
  헤더 필드, 페이지 크기와 reserve, 최소 사용 가능 공간.
