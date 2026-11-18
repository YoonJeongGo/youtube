# 공개 소스 스냅샷과 검증 범위

기준은 [초기 소스 커밋 `ee42e0401fe6`](https://github.com/YoonJeongGo/youtube/commit/ee42e0401fe65fdf145caa6d3397919c3baba427)의 **43개 파일**이다. [source_manifest.json](../source_manifest.json)은 해당 커밋의 실제 Git blob 바이트를 `git cat-file blob`으로 읽어 계산한 SHA-256을 기록한다. 후속 커밋이 추가돼도 이 문서의 기준 커밋은 자동으로 바뀌지 않는다.

## 해시와 줄바꿈

Git에 저장된 소스는 합계 **713,189바이트**, 당시 검사한 작업 사본은 **716,373바이트**다. 35개는 원시 바이트가 같고 아래 8개는 CRLF→LF 정규화만 다르다. 모든 Git blob의 CRLF와 단독 CR은 0개이며, 당시 작업 사본의 CRLF를 LF로 바꾸면 43개 모두 커밋 내용과 일치한다. 검사 입력과 커밋 사이에 줄바꿈 이외의 내용 차이는 없다.

| 파일 | 검사한 작업 사본 바이트 | Git blob 바이트 |
|---|---:|---:|
| `AGENTS.md` | 43967 | 43766 |
| `README.md` | 40126 | 39889 |
| `docs/automatic_planning.md` | 20647 | 20515 |
| `docs/production_rules.md` | 96418 | 95606 |
| `episode_core.py` | 15575 | 15282 |
| `make_vs.py` | 32938 | 32318 |
| `plan_matchups.py` | 29493 | 28957 |
| `tests/test_renderer.py` | 21519 | 21166 |

`git_blob_sha256`이 공개 커밋 검증의 기준이다. `git_blob_oid`는 Git 자체의 별도 객체 ID이며 파일 내용의 SHA-256과 다르다. `tested_worktree_sha256`은 커밋 전 검사에 쓴 바이트이고 `origin_source_snapshot.sha256`은 공개 사본을 만들기 전 원본의 해시다. 원본과 공개본은 README 안내 추가, 제외 규칙 보강, 개인 홈 경로의 자리표시자 치환 또는 허구 예시 작성 때문에 의도적으로 다를 수 있다. 변환과 각 줄바꿈 수를 파일별로 기록했다. 원본 파일·실행 환경·인증정보·개인 절대경로 자체는 manifest에 넣지 않는다.

초기 커밋 이후 공개 보관 승인과 공유 규칙 확인을 반영하여 `docs/decision_log.md`, `docs/shared_system.md`가 갱신됐다. 그 **후속 내용은 이 manifest의 초기 커밋 해시 범위에서 제외**한다. manifest의 작업 사본 관찰은 작성 시점의 비교 기록이며 두 문서가 계속 미커밋 상태라는 뜻이 아니다. 두 파일의 당시 검사 입력은 기록된 SHA-256과 정확히 일치하는 초기 Git blob으로 확인했다.

manifest 자기 자신, 이 설명 문서, 별도 규칙 검토 문서, 후속 아카이브 목록·배포 기록·Release ZIP도 고정된 43개 소스 파일 목록에서 제외한다. 아카이브는 해당 catalog/Release의 별도 해시로 확인한다.

## 실제 수행한 검사

- 공개 staging에서 `python -B -X utf8 -m unittest tests.test_detail_profile tests.test_detail_integration -v`: **20개 통과**, 테스트 러너 기록 0.336초. 신규 v5 선택, 저장된 v4 재개 보존, 14 cue·9개 화면 그림·8개 낭독 그룹, 글자 수·CTA, 미지원 조합 거부, WordBoundary·PCM·SRT와 헤더 메타데이터 연결을 검사했다.
- `python -B -X utf8 check_episode.py v5_schema_example.json --text-only`: 형식 검사 통과. 이 예시는 원전이나 제작 승인 자료가 아니다.
- 공개 사본의 Python 문법과 로컬 import 연결을 확인했고 누락된 로컬 모듈은 0개였다. 당시 43개 파일의 민감정보·개인 홈 경로 패턴 검사에서 발견 후보는 0개였다. 패턴 검사는 모든 비밀정보가 없다는 보증이 아니다.
- 위 검사는 초기 커밋 전 staging에서 수행했다. 이번에는 그 검사 입력의 해시와 실제 Git blob을 직접 비교했다. 문서 작성 중 20개 검사를 새로 실행했다고 주장하지 않는다.

테스트는 서비스·렌더 호출을 막고 합성 PCM을 메모리에서 다루거나 임시 메타데이터/SRT/상태 파일을 사용한다. 이미지·TTS·최종 영상 생성, 모델 다운로드, 새 환경 설치, 다른 운영체제 검사, 전체 회귀검사와 실제 청취는 수행하지 않았다. 통과 결과는 영상 품질 PASS나 업로드 승인이 아니다.

## 설치와 작업 복원

1. [README](../README.md)의 설치 안내에 따라 가상환경과 `requirements.txt`를 준비한다. 실제 설치 환경과 서비스 가용성을 다시 확인하며 빈 작업장 초기화와 기존 이력 복원을 구분한다.
2. **FFmpeg/FFprobe** 실행 파일과 **한글 글꼴**은 포함하지 않았다. PATH에서 두 실행 파일을 찾을 수 있어야 하며 `make_vs.FONT_CANDIDATES`의 Windows 맑은 고딕 또는 Linux Noto 후보 중 실제 설치된 글꼴을 확인한다. 필요한 글꼴 설정은 자신의 환경에 맞춰 명시적으로 변경한다.
3. 선택적 ASR·DNSMOS·Praat·Qwen 검사기 소스는 있지만 모델 가중치·실행 바이너리·전체 분석 환경은 제공하지 않는다. 선택한 도구의 의존성과 모델 출처·revision·파일 해시·라이선스·승인 범위를 확인한 뒤 별도로 준비한다. 기본 requirements 설치만으로 모든 선택 검사기가 재현되지는 않는다. 모델 다운로드를 묵시적으로 실행하지 않는다.
4. Codex의 원전 조사·내장 imagegen·실제 시각/음향 검수는 별도 세션 기능이다. Python 소스만으로 이를 무인 재현할 수 없다. 현재 규칙과 전제·전체 대본 승인 범위를 먼저 확인한다. Gemini 금지와 유튜브 업로드 금지는 유지한다.
5. 편집 원본과 작업 이력은 [아카이브 목록](../archives/catalog.json)에서 실제 보관 범위·버전·검수 상태를 확인하고 [Releases](https://github.com/YoonJeongGo/youtube/releases)의 해당 ZIP으로 복원한다. 완성본 ZIP과 편집 원본/이력 ZIP은 목적이 다르다. 별도 폴더에 먼저 풀어 파일 해시·작업 ID·원전·대본·승인 기록·기존 프로필을 확인하며 현재 자료를 무조건 덮어쓰지 않는다. `NEEDS_REVIEW`와 `AUDIO_REVIEW_REQUIRED`는 복원만으로 해소되지 않는다.

## Git blob 해시 재확인

저장소 루트에서 아래 읽기 전용 검사를 실행할 수 있다. 작업 사본의 줄바꿈 설정 대신 manifest가 가리키는 **고정 커밋**의 바이트를 검사한다. Git 객체가 없는 소스 ZIP만 받은 경우에는 먼저 해당 커밋이 있는 Git checkout을 준비해야 한다.

```python
import hashlib
import json
import subprocess
from pathlib import Path

manifest = json.loads(Path('source_manifest.json').read_text('utf-8'))
commit = manifest['source_commit']
for entry in manifest['files']:
    oid = subprocess.check_output(
        ['git', 'rev-parse', commit + ':' + entry['path']]
    ).decode().strip()
    assert oid == entry['git_blob_oid'], entry['path']
    blob = subprocess.check_output(['git', 'cat-file', 'blob', oid])
    assert len(blob) == entry['git_blob_bytes'], entry['path']
    assert hashlib.sha256(blob).hexdigest() == entry['git_blob_sha256'], entry['path']
print('Verified', len(manifest['files']), 'source blobs')
```
