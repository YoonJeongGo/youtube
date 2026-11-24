# 숏폼·롱폼 제작 자료

[숏폼 18편](shorts/README.md) · [롱폼 2편](longform/README.md) · [공통 시스템·작업 이력](archives/2026-11-24/README.md) · [전체 보관 목록](archives/catalog.json)

2026-11-24 현재 작업본을 형식별로 구분해 보관했습니다. 영상·자막·썸네일은 편별 전달 ZIP, 그림·음성·대본·검수·수정 이력은 편집 원본 ZIP에 있습니다. 실제 전달 폴더의 이전 버전도 형식별 이력 묶음에 보관합니다.

큰 파일은 GitHub Releases에 있으며, 이 저장소에는 코드·제작 규칙·목록과 복원 도구를 둡니다. [복원 방법](archives/RESTORE.md)을 확인하세요. 기존 [2026-11-18 보관본](https://github.com/YoonJeongGo/youtube/releases/tag/archive-2026-11-18)도 유지했습니다.

현재 검수 상태는 편별 목록에 그대로 표시합니다. GitHub 보관과 영상 품질 검수는 별개입니다.

## 2026-11-19 롱폼 서사 시스템 개정: 전체 9~15분·전투 약 3분

롱폼은 [새 공용 규칙](docs/longform_production_rules.md) → [기획 프롬프트](docs/longform_planning_prompt.md) → [대본 검수 항목](docs/longform_script_review.md) 순서로 적용한다. [사용자 개정 원문](docs/requests/2026-11-19-longform-story-system-request.txt)을 보존했으며, 아래 이전 롱폼 지침과 충돌하면 이번 개정이 우선한다. 쇼츠 기준은 유지한다.

- 실제 전체 **9~15분**, 전투는 **최종 편집 기준 약 3분을 기본 목표**로 함께 설계한다. 180초 강제·고정 비율·공방/이미지 개수·반복으로 시간 채우기를 금지한다.
- **도입 → A·B 이해 → 한 인물의 진행 중인 행동 → 발단 → 갈등 전개 → 위기 → 결정적 충돌과 절정 → 결말·질문**으로 이어진다. 모든 장면은 정보·관계·위험·목표 상태·선택을 바꾸고, 필요한 관찰·행동·직접 대사를 구체적으로 보여 준다.
- 공방의 결과가 다음 판단을 바꾸게 하며 원전 능력의 임의 제한·새 약점·억지 우세 교대를 금지한다. 결말 분위기는 고정하지 않되 중심 사건의 결과는 명확히 한다.
- 전체 대본/전제·씬별 시간·전투 시작/끝/합계·공방표·이미지 대응·원전/창작 구분을 먼저 제출한다. 승인 후 이미지 전체 초안과 등장 순서 검토, 수정·재검수, 음성·자막·편집을 진행한다. 최종 전체/전투 실측과 남은 문제를 보고하고 사용자 전체 청취 전 음성은 승인 대기다.
- 이번 시스템 갱신만으로 특정 편의 대본·미디어·승인 이력을 변경하거나 제작을 시작하지 않는다. 다른 채팅이 이미 숙지했다고 주장하지 않는다.

# 대결 쇼츠·롱폼 제작 시스템

2026-11-18부터 **쇼츠와 롱폼을 별도로 운영**한다. 신규 쇼츠의 기본 `showdown_detail_v5`와 기존 편의 프로필은 유지한다. 롱폼은 `format: longform`, `production_profile: showdown_story_long_v1`이며 전체 운영 기준은 [롱폼 공용 제작 규칙](docs/longform_production_rules.md)에 있다. [자동 기획](docs/automatic_planning.md), [공유 시스템](docs/shared_system.md), [결정 기록](docs/decision_log.md)도 함께 확인한다.

- 롱폼: 최종 실제 540~900초, 1920×1080·30fps, H.264/yuv420p·AAC, 가변 씬/컷/cue/낭독 그룹, 중심 사건의 결과가 있는 이야기.
- 공통: 기존 화풍·화자와 정지 그림, BGM·줌·이동·흔들림 기본 없음, 원전·판본·금지 기준, 대본·전제 승인, 실제 검수·수정 한도·해시, D 저장·전달.
- 롱폼의 필수 기술 검사와 최종 음성 승인은 별개다. 기술·시각 준비 후 검수용 전달 → 사용자의 최종 MP4 전체 청취 승인 → 나머지 필수 조건·전달 확인 → COMPLETE다. 로컬 음성 모델은 선택이며 사용자 승인을 대신하지 않는다.
- 아래 기존 제작 예제·고정 이미지/cue·세로 규격·승자 미정·썸네일·태그·음성 모델 완료 기준은 **쇼츠 경로**다. 롱폼에 그대로 적용하지 않는다. 생성·렌더 예제는 유효한 해당 대본·전제 제작 승인이 있을 때만 사용한다.
- 이번 공용 시스템 구축은 특정 편 예약·실제 대본·이미지·TTS·영상 제작을 포함하지 않는다. 문서 저장과 mock/fixture 통과는 실제 9~15분 통합 생성·시각 검수·음성 청취 완료가 아니다.


## 롱폼 실행 경로와 기록

이 절은 **이후 실제 롱폼 제작 요청**의 사용 안내다. 이번 시스템 구축에서는 아래 선택·생성·합성·전달 명령을 실행하지 않는다. 모든 명령은 `D:\유튜브 자동 쇼츠 생성`에서 실행한다. 경로·ID 변수에는 실제 해당 작업의 값을 사용하고, 특정 인물·첫 작품·기본 줄거리는 저장하지 않는다.

### 현행 기획·검수 지침과 전투 시간 보고 (2026-11-19)

새 롱폼 기획 전에 아래 읽기 전용 명령으로 실제 공용 기획 프롬프트와 대본 검수 항목을 불러온다. 후보 선택 결과와 롱폼 대본 검사에도 같은 문서 경로가 표시된다. 명령 성공은 대본 내용의 품질 판정이 아니다.

```powershell
.\run_python.ps1 plan_matchups.py longform-guidance

# longformEpisode는 실제 해당 episode.json 경로
# combatCueRange는 검토자가 대본을 읽고 정한 '시작cue:종료cue' 문자열
.\run_python.ps1 check_episode.py $longformEpisode --longform-timing-report --combat-range $combatCueRange
```

여러 전투 구간은 `--combat-range`를 반복 지정한다. 양끝 cue를 포함하며 겹치는 시간은 한 번만 센다. 도입 재탕·긴 회상·반복 설명은 전투 합계에 부풀려 넣지 않는다. cue 중간에서 공방이 바뀌면 경계의 한계를 밝히고 필요한 대본/편집 경계를 조정한다.

보고서는 기획 추정·현재 대본에 대응하는 시간표·최종 MP4 측정값을 구분한다. 구간 미지정은 미분류, 파일 미생성은 미측정이며 0초나 PASS로 바꾸지 않는다. 시간표 해시/현재 cue 순서를 확인하고 원본 경로·해시를 남기지만, 같은 길이라고 최종 영상과 시간표의 내용이 같다는 뜻은 아니다. 실제 영상 내용과 구간 대응을 검토한다. 전투 약 180초는 참고 목표이며 자동 합격 기준이 아니다. 전체 540~900초 및 기존 최종 검수 조건은 유지한다.

### 기획과 재개

```powershell
.\run_python.ps1 plan_matchups.py status --format longform

# 이후 제작 요청에 따라 조사한 후보가 준비됐을 때만 작업 선택/예약
.\run_python.ps1 plan_matchups.py select --format longform

# 지정 조합 또는 기존 사건을 이어 갈 때 실제 기록된 값을 전달
.\run_python.ps1 plan_matchups.py select --format longform --matchup $matchup --story-id $storyId
```

- `--format`을 생략한 기존 명령은 쇼츠 경로를 유지한다. `--format longform`은 별도 `matchup_ideas_longform.json`, `queue_longform.txt`, `done_longform.txt`, `state/project_state_longform.json`, `episodes/longform/`을 사용한다. 이번 저장만으로 실제 작업을 예약하거나 이 파일들에 특정 후보를 넣지 않는다.
- 롱폼 대기열/완료 목록은 `story_id<TAB>A vs B`로 사건을 구분한다. 같은 대진 쇼츠는 별도 형식이다. 재개는 기존 `job_id`, `story_id`, `episode_path`를 유지한다.
- `select`에는 기존 `--count`, `--run-id`, `--new`, `--remake`도 연결되어 있다. 새 사건 요청과 재제작 요청을 구분하고, 이어 가는 실행은 같은 형식·run ID·편수·사건을 유지한다. `finish-run`, `set-status`도 `--format longform`을 사용한다. 명령을 실행하기 전에 현재 단계·잠금·승인 범위를 확인한다.
- 롱폼 후보에는 정확한 형식/프로필, 안정된 `story_id`, 9항목 `story_plan`, 범위 안의 `planned_duration_seconds`를 기록한다. 이는 추정·계획이며 최종 MP4 실측 통과를 대신하지 않는다. 후보가 부족하면 `NEEDS_RESEARCH`를 따라 Codex가 조사·구성하고, 사용자에게 항목별로 정해 달라고 떠넘기지 않는다.
- 같은 대진의 새 사건은 이전 사건과 다른 `narrative_signature` 및 실제 비교 검토 `narrative_review`의 `status`, `reviewed_at`, `differences`, `compared_story_ids`가 필요하다. 해시 차이만으로 서사의 실질적 차이를 입증하지 않는다.

### 대본·매체 연결

데이터 구조와 제작 승인 필드는 [롱폼 데이터 계약](docs/longform_production_rules.md#실제-데이터-계약과-제작-승인-연결)을 따른다. 씬/컷/cue/낭독 그룹을 독립 ID로 연결하며 모든 본문 cue 뒤 공통 `cta`를 정확히 한 번 포함한다. 실제 원전·창작 경계·인과·상태·이미지 수·재사용/신규 수·낭독 그룹 수를 기획과 함께 검토한다.

```powershell
# longformEpisode는 기존/채택 작업의 실제 episode.json 경로
.\run_python.ps1 check_episode.py $longformEpisode --text-only
.\run_python.ps1 make_voice.py $longformEpisode --plan-only
```

`--text-only`는 대본 구조 검사이고 `--plan-only`는 음성 설정 계획이다. 사실·자연스러운 문체·이미지 품질·실제 발화 길이가 검증된 것으로 보고하지 않는다. 승인 전 실제 이미지·TTS·렌더는 하지 않는다. 전체 대본·전제 승인 근거와 현재 해시를 `production_authorization.json`에 기록하며 유효한 승인 없이 매체 생성 경로를 진행하지 않는다.

```powershell
# 실제 전체 대본·전제 제작 승인 후에만 실행
# 내장 imagegen으로 생성하고 직접 확인한 원본을 해당 컷에 비례 유지해 저장
.\run_python.ps1 asset_tools.py save $generatedSource $assetDestination --episode $longformEpisode --prompt-file $promptFile --generator 'built-in image_gen'
.\run_python.ps1 check_episode.py $longformEpisode
.\run_python.ps1 make_voice.py $longformEpisode
.\run_python.ps1 make_voice.py $longformEpisode --check
.\run_python.ps1 make_vs.py $longformEpisode
```

- `asset_tools.py --episode`는 그 롱폼이 선언한 동적 이미지 목록을 사용한다. 인물 기준 그림은 1024×1024, 씬/컷/썸네일은 1920×1080에 비례 유지와 필요한 여백으로 저장하며 몸을 늘이지 않는다. 기존의 검수된 인물 기준은 기존 크기·해시로 재사용할 수 있고 실제 컷에서 사용할 때도 비율을 보존해 화면을 확인한다. 저장 성공은 실제 원본 전체·손/관절/무기·동작·크롭 검수 PASS가 아니다. 기존 쇼츠 자산 규격은 유지한다.
- 렌더는 형식별 설정을 선택한다. 쇼츠의 `render_settings.json` 세로 좌표를 롱폼 가로 화면에 복사하지 않는다. 필요한 가로 레이아웃 조정은 `render_settings.json`의 `longform` 영역에서 관리한다.
- 기존 묶음 TTS·WordBoundary·PCM·자막/SRT·동일 시간표를 재사용한다. 전체 누적 샘플 기준 시간과 프레임 정렬을 검증하고, 각 그룹의 반올림 오차를 누적하지 않는다. 540~900초 범위는 모든 구간을 포함한 최종 MP4로 다시 확인한다.

### 챕터·연계 쇼츠·썸네일

- 선택 `chapters` 항목마다 `title`과 `cue_id`/`cut_id`/`scene_id` 중 한 기준을 지정하면 실제 최종 시간표의 그 시작 위치에서 타임스탬프를 계산한다. 여러 씬을 한 챕터로 묶을 수 있다. 지정하지 않으면 씬 전환과 씬의 의미 있는 `title` 또는 `objective`에서 만들며, 설명 파일에는 실제 계산값을 기록한다.
- 선택 `planning.related_short`에는 실제 기존 쇼츠 작업 ID·경로·`continuation`/`new_event` 구분·판본/연속성 검토·질문 유지/변경 이유·읽은 원전/인물 기준/에피소드 해시를 연결한다. 최종 완료 시에만 해당 관계에 맞는 `linked_short_comment.txt` 초안을 전달한다. 실제 URL이 없으면 `[롱폼 영상 링크]`를 유지하며 게시·댓글 등록은 하지 않는다.
- `cfg.thumb`의 실제 파일을 전달 썸네일로 사용한다. 별도 `upload_thumbnail.png`를 쓰면 그 현재 검수에도 `full_observations`, `detail_observations`, `small_size_observations`, 미해결 문제 없는 `issues`와 원본/결과 해시가 필요하다. 원본 썸네일을 확인했다는 사실로 다른 대체 썸네일을 통과시키지 않는다.

### 롱폼 음성 기록과 검수용 전달

별도 음성 승인 서비스를 만들지 않고 기존 `audio_review.json`, `qa_signoffs.json`, `qa_report.md`, `manifest.json`을 재사용한다. 기존 상위 `audio_review.input_hash`와 `pcm_sha256`도 현재 시간표·원본 PCM과 연결해 유지한다. 다음은 **실제 검수·실제 사용자 발언을 기록할 필드 안내**이며 그대로 복사해 PASS/승인을 만드는 양식이 아니다.

- `audio_review.technical`: 실제 기술 검수의 `status`, 현재 `video_sha256`, `timeline_sha256`, 최종 MP4를 48kHz 스테레오 s16le PCM으로 실제 디코딩한 `final_audio_sha256`, `audio_timing_hash`, 실제 `asr_file/asr_sha256`, `checks`를 연결한다. 검사 항목은 `asr_alignment`, `starts_and_ends`, `group_boundaries`, `volume_clipping_silence`, `audio_specs`, `subtitle_srt_alignment`다. 각 항목은 구체적인 `observations`, 미해결 `issues`, 실제 `evidence` 파일/해시를 기록한다. ASR의 누락·중복·순서·오독 의심은 실제 근거와 대조하고 미해결 상태를 감추지 않는다.
- `audio_review.user_approval`: 사용자가 **최종 MP4 전체를 듣고 승인한 경우만** `status: APPROVED`, `reviewer_kind: user`, `scope: full_final_mp4`, `full_listened: true`, 실제 `statement`, `approved_at`, 승인한 `video_sha256`, `final_audio_sha256`, `audio_timing_hash`를 기록한다. 특정 문구 입력을 강제하지 않고 문맥과 대상 버전·전체 청취 범위를 확인한다.
- `qa_signoffs.audio`는 해당 사용자 승인 기록의 해시와 현재 영상/음성/시간표를 연결한다. Codex가 들었다고 기록하지 않는다. 로컬 음성 이해 모델은 선택적 보조이며 필수 실행이나 사용자 승인 대체 경로가 아니다.
- 그림/자막/메타데이터만 바꿨고 실제 음성과 시간 배치가 동일하면 기존 사용자 승인 범위를 유지한다. 영상 해시가 달라진 경우 승인받은 이전 MP4를 보존하고 `approved_video_path`로 연결해 이전 영상 해시와 실제 디코딩 음성 및 현재 음성/시간 배치가 일치하는지 확인한다. 말·순서·발음·속도·음량·쉼·시간 배치가 달라지면 새 최종 버전의 전체 청취 승인이 필요하다.
- 그림/자막만 바뀐 뒤 기존 ASR를 재사용할 때는 원래 분석 JSON을 그대로 보존하고 `technical.asr_reuse`에 `source_video_path`, `source_video_sha256`, `source_timeline_path`, `final_audio_sha256`, `audio_timing_hash`를 연결한다. 보존한 이전 MP4의 실제 디코딩 음성과 이전 시간표의 발화 내용·시간 배치가 현재와 같아야 한다. 과거 ASR의 영상/시간표 해시를 새 값으로 고쳐 재검사한 것처럼 기록하지 않는다. 현재 최종 MP4의 다른 기술·시각 검사는 별도로 유지한다.
- 전제·대본 제작 승인, 파일 수령, 일부 구간 청취는 전체 MP4 음성 승인이 아니다. 미실행 필수 검사는 `NOT_RUN`, 사용자 전체 청취 승인 전은 `NEEDS_REVIEW`이며 일반적인 과거 수락 기록으로 우회하지 않는다.

```powershell
# 실제 기술·시각·자막 검사 근거를 기록한 뒤 현재 상태 검사
.\run_python.ps1 check_episode.py $longformEpisode --subtitles
.\run_python.ps1 check_episode.py $longformEpisode --quality

# D의 storage_config.json 전달 경로로 검수용/완료 결과 전달
.\run_python.ps1 deliver_episode.py $longformEpisode

# 실제 전체 청취 승인과 필수 검사 통과 후 전달을 갱신하고 완료 확인
.\run_python.ps1 deliver_episode.py $longformEpisode
.\run_python.ps1 check_episode.py $longformEpisode --completion
```

최종 음성 승인 대기 중 `--quality`/`--completion` 통과를 꾸미지 않는다. 별도 review-ready 옵션은 없다. 전달 명령은 전체 검사 후 `LONGFORM_LISTENING_PENDING` 또는 `LONGFORM_LISTENING_STALE`만 남은 경우에 검수용 전달을 허용한다. 기술·이미지·자막 등 다른 실패는 롱폼 전달을 막는다. 파일 전달 성공과 COMPLETE는 별개다. 검수용 MP4를 먼저 제공한 뒤 그 버전 전체 청취를 기다린다. 명백한 기술 실패나 미검수 그림을 정상 검수대기 음성 승인만 남은 것처럼 설명하지 않는다. 사용자의 실제 승인 후 다시 전달·해시 확인하고 모든 조건이 충족된 편만 COMPLETE와 done에 반영한다. 게시·댓글 등록은 하지 않는다.

이번 구축의 검증은 생성 없는 fixture/mock/형식 검사 범위다. 실제 9~15분 TTS·이미지·통합 렌더, 실제 시각 검수와 전체 음성 청취는 **NOT_RUN**이다. 실제 실행한 테스트와 남은 제한은 이번 보고를 확인한다. 문서의 명령 예시는 실행 성공 기록이 아니다.

## 2026-11-18 실제 저장 위치 확정

- 작업의 기준 경로는 `D:\유튜브 자동 쇼츠 생성`이다. 새 파일·설정·기획·수정·실행은 D 경로에서 한다.
- C의 기존 프로젝트 루트와 `.git`는 실행 중인 앱의 잠금으로 유지했다. 하위 작업 폴더는 D로 연결한다. C 루트 전체가 junction인 것은 아니다.
- C 루트의 작은 기존 코드/문서는 호환 잔여본이다. 이를 최신 기준으로 편집하거나 실행하지 말고 D의 실제 파일을 사용한다.
- 기존 방을 계속 사용해도 작업 시작 시 D로 이동하고 D의 `AGENTS.md`와 `docs/production_rules.md`를 읽는다. Python 실행은 D의 `run_python.ps1`를 사용한다.
- 실제 전달본은 `D:\유튜브 자동 쇼츠 생성\전달본`, 모델·임시 파일·캐시도 D를 사용한다. Codex 앱 자체의 대화 DB/설정/첨부는 이번 이전 대상이 아니다.

## 최신 저장 위치: D드라이브 (2026-11-18)

사용자가 “현재 작업을 로컬 D에 복제하고 C 용량을 비우며, 모든 저장을 D에, 폴더 이름은 C와 동일하게” 요청했다.

- 정식 작업 폴더는 `D:\유튜브 자동 쇼츠 생성`이다. 코드·원본·이미지·음성·영상·검수·수정 이력·프로젝트 모델/가상환경·프로젝트 임시파일을 이 폴더에 저장한다.
- 새 전달 기본은 `D:\유튜브 자동 쇼츠 생성\전달본\<해시태그까지 포함한 전체 유튜브 제목>`이다. 제목/태그/폴더명 일치 규칙은 유지하고 **기존 C 바탕화면 직접 전달 규칙보다 이번 지시를 우선한다.** `storage_config.json`이 이 위치를 강제한다. 전달 시 `--desktop-root`를 새로 지정하지 않는다.
- 이전 C 프로젝트 경로와 이전한 기존 전달 경로는 D를 가리키는 Windows junction으로 호환한다. C 경로가 보여도 실제 파일은 D에 있다. junction을 중복 원본으로 취급해 재복사/재귀 삭제하지 않는다.
- 실행 전에 `. .\environment.ps1`로 프로젝트 전용 임시/패키지 캐시 위치를 적용한다. 표준 실행은 `.\run_python.ps1 <스크립트> <인수...>`이다. C의 공용 Python/FFmpeg/Windows 글꼴은 읽기용 기존 설치이며 프로젝트 이전을 이유로 삭제하지 않는다.
- 기존 에피소드의 원전·승인·검수 상태·프로필과 과거 절대경로 기록을 일괄 수정하지 않는다. 이전 내역은 `.local-work/migration-20261118/`에 보존한다. 검증 전 복사 상태는 이전 완료가 아니다.
- Codex 앱 자체의 채팅 DB·설정·첨부 보관소는 이 프로젝트 폴더와 별개다. 이 지시로 앱 데이터 전체를 임의 이동하거나 삭제하지 않는다. 기존 F 드라이브 ZIP도 유지한다.

# 대결 쇼츠 제작 작업장

원전을 확인한 두 인물의 구체적인 가상 대결을 이야기로 기획하고, 신규 v5의 기본 화면 9장·14 cue·한 호흡의 묶음 음성·자막을 세로 쇼츠로 합성한다. 대표를 소개에 직접 쓰면 자산 9장, 별도 소개 호환 경로는 기준 그림 포함 11장이다. 사진은 고정하며 사진이 바뀔 때만 사진 영역에 짧은 전환을 넣는다. **유튜브 업로드나 댓글 등록은 하지 않는다.** 제작 전 [AGENTS.md](AGENTS.md)와 [상세 제작 규칙](docs/production_rules.md)을 읽는다.

Python은 후보·작업 이력, 파일, TTS, 합성, 기술 검사를 담당한다. 현재 Codex 세션이 실제 원전 조사, 내장 `imagegen`, 이미지 관찰과 음성 검수를 담당한다. **Codex가 직접 음성을 확인할 방법을 적극적으로 찾아 시도하고, 발견한 문제를 수정한 뒤 최종 저장까지 진행한다. 모든 영상에는 화면 자막을 반드시 넣는다.** Python만으로 내장 도구를 무인 호출하는 프로그램은 아니며, 직접 확인하지 않은 내용을 검수 완료로 표시하지 않는다.

## 신규 기본: showdown_detail_v5 (2026-11-18 확정)

v5는 구체적 행동과 직접 결과가 같은 사건 안에서 이어지는 이야기다. 전체 기준은 [v5 상세 제작 규칙](docs/production_rules.md#showdown-detail-v5)에 모아 둔다. `schema_version: 2`, `winner: "none"`, `cta_presentation: "comment_spoken"`, 30~60초·목표 약 40~55초와 기존 검수·전달 규칙은 유지한다.

- 기본 흐름은 구체적 hook 1 cue → A 소개/원전 활약 2 cue → B 소개/원전 활약 2 cue → setup 2 cue → 공방 1/2 각 2 cue → 마지막 행동/질문 2 cue → 기존 댓글 안내 1 cue다. 마지막 그림을 댓글 요청까지 유지해 화면 그림은 9종이다.
- 기존 `a.edge`/`b.edge`는 같은 사건의 순차 공방을 담는다. `reason`은 마지막 행동이다. v5의 edge.lines는 각 48자, reason은 48자이며 v4의 44자/40자 제한을 바꾸지 않는다. 기존 필드·자산·기록을 재사용한다.
- 인물당 단독 소개+활약 2 cue를 같은 TTS 그룹으로 읽고 원전의 어려움→행동→결과를 뒤 공방에 연결한다. 소개 약 4~6초는 계획 참고치이며 실제 길이·음향 품질은 생성 후 별도 확인한다.
- 신규 선택 기본은 v5로 연결하되 기존 작업 재개는 저장된 프로필을 보존한다. 이번 지정 달타냥 vs 시라노 초안 외의 진행·완료 편을 일괄 전환하지 않는다.
- v5는 검증된 묶음 낭독·WordBoundary·PCM·외곽 트림·시간표 경로를 사용해야 한다. 지원되지 않는 조합을 구형 CTA·단문·무음으로 몰래 낮춰 통과시키지 않는다.
- 이번 기본 v5에서는 기존 선택 `clash`, `additional_edges`, `story_beats` 혼합을 지원 대상으로 사용하지 않는다. 선택 공방·추가 컷 기능은 확장하지 않으며 미지원 조합은 명시적으로 거부한다. 기존 v4 선택 기능은 아래에 보존한다.
- 형식 검사는 14 cue·순서·이미지·글자 수·낭독 그룹·CTA를 다룬다. 추상 표현 경고는 cue ID와 표현을 알려주는 보조이며 단어 하나로 실패시키거나 경고 없음으로 내용 PASS를 부여하지 않는다. 의미·원전·인과·그림 상태는 기존 storyboard/출처/QA에서 검토한다.

**이번 실행은 최소 문서·코드 반영, 생성 없는 관련 검사, 달타냥 vs 시라노의 전제·전체 대본·장면표·공방표·근거 제출 후 승인 대기까지다.** 승인 전 무료/유료 이미지 생성·TTS 요청·최종 영상 합성을 하지 않는다. 대진 선택만으로 구체적 전제나 제작이 승인된 것이 아니다. 아래 생성·렌더·전달 명령은 승인 후 절차의 안내이며 이번에 실행하라는 지시가 아니다.

승인 전에는 기존 fixture를 이용하는 생성 없는 검사를 고르고, 명령의 실제 부작용을 먼저 확인한다. `--text-only` 성공은 사실·문체·이미지·음성·실제 영상 길이 검수를 대신하지 않는다. 문서의 지원 설명 자체는 실행 테스트 결과가 아니며 수행한 검사만 별도 보고한다.

## 기존 프로필: showdown_story_v4 (2026-11-16 확정·호환 보존)

아래 절은 v4로 저장된 편의 재현 기준이다. 당시의 “신규 기본” 표현을 현재 v5에 적용하지 않는다. 기존 편을 수정하거나 v5 요구를 소급하지 않는다.

2026-11-16 사용자가 공통 제작 시스템과 장기 메모에 남기도록 요청한 최종 구성이다. 아래와 충돌하는 v3/이야기형 규칙은 과거 편 재현용이다. 새 편과 명시적으로 재제작하는 편에는 `production_profile: "showdown_story_v4"`, `schema_version: 2`, `cta_presentation: "comment_spoken"`을 적용한다. 기존 편을 요청 없이 다시 만들지 않는다.

1. **시작**: 단순 직업이나 기술명만 나열하지 말고, 인물의 대표성·위력·무대가 떠오르는 짧은 별칭 또는 수식어로 대립을 연다. 예: “신출귀몰한 의적” / “셔우드 숲의 명궁”. 원전에 근거가 있어야 하며 공식 별명과 편집상 소개 문구를 구분한다. 근거 없는 최강·불패·백발백중을 만들지 않는다. VS 그림에 본명이 보이면 낭독에서 이름을 반복하지 않아도 된다. 질문은 “누가 이길까요?”로 끝낸다. 썸네일 원본에는 이름 둘과 VS만 둔다.
2. **A 소개 → 대표 활약**: A 혼자 있는 그림으로 누구인지 말하기 시작하고, 같은 문장의 호흡을 유지한 채 어려운 상황에서 능력을 발휘한 대표 활약 그림으로 넘어간다.
3. **B 소개 → 대표 활약**: 같은 방식으로 B의 강점을 충분히 보여 준다. 평범한 연습·기초 능력·과녁 명중만으로 대표 활약을 대신하지 않는다. 대결에 관련된 전투/위기 극복/난도 높은 일화를 우선한다.
4. **자연스러운 가정**: 소개한 특징에서 이어지는 말로 한쪽이 자기다운 일을 하고 상대가 막는 구체적 상황을 만든다. “가상 상황” 안내 카드를 끼우지 않고 “~라면” 등 발화로 가정임을 분명히 한다.
5. **A 조건 → B의 반대 조건**: 각자의 강점이 실제로 작용하는 별도 행동 그림을 사용한다. 원전 일화를 또 낭독하지 않고 그 능력이 현재 장면에 주는 차이를 설명한다.
6. **새 결말 그림과 마지막 질문**: `cliffhanger.png`에 양쪽이 동시에 강점을 발휘하는 대립을 새로 그린다. 결과 직전에 멈추고 처음의 구체적 상황으로 돌아가 질문한다. 이미 조준을 끝냈다는 식으로 한쪽의 대응을 지워 놓지 않는다. 승자와 명중 결과를 보여 주지 않으며 winner="none"이다.
7. **댓글 안내만 낭독**: 마지막 질문 뒤 “여러분의 생각을 댓글로 남겨 주세요.”를 정확히 한 번 읽는다. “좋아요와 구독도 부탁드려요”는 화면에만 표시한다. `cta_presentation: "comment_spoken"`을 신규 기본으로 사용하고, 새 장면 대신 마지막 cliffhanger 그림을 유지한다. 화면의 공통 두 줄은 그대로 표시하며 CTA 낭독 구간에서는 이미 표시된 첫 줄을 자막으로 사용해 아래에 중복 출력하지 않는다. SRT에는 실제 읽는 댓글 안내만 추가한다.

### 대본·원전과 이미지 연결

- hook 40자, tag 14자, intro 인물당 2 cue×48자, setup.title 22자/lines 2 cue×48자, edge.title 16자/lines 2 cue×44자, reason 40자, question 36자. NFC 기준 공백·문장부호를 포함한다.
- 첫 intro cue는 문장의 앞 절, 둘째 cue는 이어지는 활약 설명이다. 두 cue는 반드시 같은 TTS 그룹에 넣는다. setup/edge도 첫 cue에서 문장을 끝내지 않아도 된다. 자막 때문에 낭독을 조각내거나 매번 ~요로 끊지 않는다. 전체 낭독 문장과 조사·종결의 자연스러움을 검수한다.
- `a/b.featured_feat`에는 summary, challenge, outcome, relevance, selection_reason, version, source.url/locator를 기록한다. 선택한 한 판본의 실제 어려움과 결과로 장점을 입증한다. 다른 판본을 섞지 않으며 근거 없는 “역대 최고/최강/무적”을 쓰지 않는다. 최고 활약이라고 입증할 수 없으면 이번 대결에 가장 적합한 대표 활약을 고른 이유를 남긴다.
- `cliffhanger`에는 image="cliffhanger.png", participants=["a","b"], winner_revealed=false, moment, unresolved_action을 기록한다. 실제 이미지 검수에 양쪽 강점과 미정 결과에 대한 관찰을 남긴다. 이전 전투 컷을 파일명만 바꿔 재사용하지 않는다.
- 기본 자산은 thumb, a/b, a_1/b_1(단독 소개), a_2/b_2(활약), scene(만남), a_atk/b_atk(조건별 행동), cliffhanger의 **11장**이다. 화면에는 9개의 서로 다른 그림이 나온다. 이미 승인된 단독 a.png/b.png가 소개에 적합하면 intro_images=["a.png","a_2.png"]처럼 연결해 기준 그림을 직접 사용해도 되며 이때 실제 필수 자산은 9장이다. 사용하지 않는 기존 그림은 보존하되 새 영상에 억지로 끼우지 않는다.
- 대표 a/b는 1024×1024, 새 장면은 1024×1536. 모든 장면은 실제 참조와 검수 해시를 남긴다. 스타일·인물 일관성·피/절단 금지·썸네일 이름/VS 규칙은 유지한다.
- 영상은 **30~60초**, 편집 목표는 약 40~55초다. 인물당 소개+활약은 약 5~8초를 출발점으로 삼되 실제 발화와 가독성을 우선한다. 장면 수를 맞추려 과속하거나 끝을 자르지 않는다. 사진 고정, 바뀔 때 사진 영역만 200ms crossfade, 공격/반응은 hardcut이다.
- 화면 자막+SRT, 실제 MP4 화면 검사, 음성 진단과 청취 한계 구분, 원전 근거, 제목·설명·댓글 동기화, 바탕화면의 제목과 같은 폴더에 전달, 업로드 금지, 최대 5편은 유지한다. 그림·음성을 바꾸면 이전 PASS를 재사용하지 않는다.

## 준비

Windows PowerShell에서 프로젝트 폴더를 기준으로 실행한다. Python, FFmpeg/FFprobe, 한글 글꼴이 필요하다. Edge TTS는 인터넷 연결과 서비스 가용성이 필요하며 새 유료 API를 대신 사용하지 않는다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ffmpeg -version
ffprobe -version
```

이미 `.venv`가 있으면 재생성하지 말고 설치 상태를 확인한다. 의존성은 [requirements.txt](requirements.txt)에 고정되어 있다. 최초 ZIP과 수정 전 파일은 `backups/original_kit_20261116/`에 보존했다.

## 대진 기획·선택

신규 기획 기본은 `recognizable_story_v1`이다. 알려진 인물, 두 인물의 특징이 맞물리는 가상 장면, 같은 사건의 A 강점과 B 대응 근거, 원전 근거가 있는 떡밥 하나와 주관적 3항목 점수(합계 12/15 이상)를 기록한다. v4 이하 후보의 반대 조건 기록은 보존한다. [기획 프로필과 검증 규칙](docs/automatic_planning.md), [과거 검토한 10개 기획안](archives/2026-11-24/README.md), [과거 검증용 JSON](archives/2026-11-24/README.md)을 참고한다. 기획 10개 작성은 제작 예약이 아니며 기존 진행 편의 형식과 검수 상태는 유지한다.

선택 우선순위는 사용자 지정 → 처리 가능한 대기열 순서 → 원전을 조사한 새 후보이다. “새 대진” 요청은 기존 이력과 중복되지 않는 후보를 고른다. 기본 1편, 최대 5편의 **시도**만 허용하며 실패·검수대기도 시도에 포함한다.

```powershell
# 기획/작업 상태 확인
.\.venv\Scripts\python.exe plan_matchups.py status

# 대기열 우선 기본 1편 선택
.\.venv\Scripts\python.exe plan_matchups.py select

# 사용자 지정 대진 선택
.\.venv\Scripts\python.exe plan_matchups.py select --matchup "홍길동 vs 로빈 후드"

# 기존 대기열과 중복되지 않는 새 대진 3편 선택
.\.venv\Scripts\python.exe plan_matchups.py select --new --count 3
```

`matchup_ideas.json`에는 실제 본문을 읽고 검증한 후보·판본·가상 조건·떡밥·제목/댓글·출처·선정 판단을 저장한다. `NEEDS_RESEARCH`는 사용자에게 선택을 떠넘길 신호가 아니다. Codex가 대기열 항목을 조사하거나 새 후보를 넉넉하게 조사해 풀을 보강한 뒤, 반환된 실행 ID와 **같은 편수**로 이어 간다.

```powershell
.\.venv\Scripts\python.exe plan_matchups.py select --run-id "반환된_실행_ID" --count 3
```

`--new` 실행을 이어 가는 경우 같은 `--new` 옵션도 유지한다. `queue.txt`는 한 줄 한 대진 그대로다. 별칭·순서가 반대인 대진·완료·실패·검수대기를 함께 중복 판정한다. 3파전·군대 대 군대는 보류하며 입력을 임의로 변형하지 않는다.

## 실패·검수대기 작업 재개

새 실행에서 같은 대진을 선택하면 FAILED/NEEDS_REVIEW의 기존 작업 ID와 폴더를 재사용한다. 완료 대진을 다시 만들려면 사용자의 명시 요청이 있어야 하며 그때만 `--remake`를 추가한다.

```powershell
# 제작 실패를 기록하고 실행을 닫은 뒤, 다음 실행에서 재개
.\.venv\Scripts\python.exe plan_matchups.py set-status "작업_ID" FAILED --reason "실제로 발생한 실패 원인"
.\.venv\Scripts\python.exe plan_matchups.py finish-run "실행_ID"
.\.venv\Scripts\python.exe plan_matchups.py select --matchup "홍길동 vs 로빈 후드"
```

IN_PROGRESS인 작업을 무조건 잠금 해제하거나 복제하지 않는다. 실제 프로세스/세션과 이력을 확인하고 중단 상태를 기록한 뒤 재개한다. 같은 실행 안에서는 이미 시도한 대진을 다시 선택하지 않는다.

## 편 제작

반환된 `episode_path`를 사용한다. 아래 경로는 명령 형식 예시다. 기존 대상의 경로가 있으면 중복 선택·예약하지 않는다. 전체 대본·전제 승인 전에는 생성 없는 검사까지만 수행한다.

```powershell
$episode = '.\episodes\001_A_vs_B\episode.json'
.\.venv\Scripts\python.exe check_episode.py $episode --text-only
```

Codex는 원전·판본·금지 기준, 대본과 문장별 근거, `character_bible.json`, `storyboard.json`을 확인한다. 전제와 전체 대본 승인 후 내장 imagegen으로 대표 이미지 2장 → 소개/활약 → 만남/공방/결말 → 썸네일을 만들고 직접 검수한다. v5 전투의 전체 구도에서는 두 사람의 위치·공격·대응을 명확히 하고, 관계가 확립된 뒤 누구의 행동인지 분명한 손·무기·핵심 단서의 근접 구도도 허용한다. 대표 직접 소개는 실제 자산 9장, 별도 소개는 기준 포함 11장이고 화면 그림은 9종이다. 각 실제 사용 파일은 episode_core.image_names로 확인한다. 대표 2장은 1024×1024, 나머지는 1024×1536이다.

썸네일에는 **두 인물의 이름과 VS만** 넣는다. “누가 이길까?” 같은 하단 질문이나 별명·설명 등 추가 문구는 넣지 않는다. 생성 프롬프트와 실제 이미지 검수에 동일하게 적용한다. 이름은 가장자리에 붙이지 않고 A는 위쪽 중앙 안쪽, VS는 중앙, B는 아래쪽 중앙 안쪽에 배치한다. 원본 썸네일과 실제 첫 VS 프레임을 목록 크롭·축소 화면까지 직접 확인한다. 자세한 위치와 검수는 [썸네일 이름 배치 규칙](docs/production_rules.md#최신-사용자-지정-썸네일-이름-배치와-목록-크롭-검수-2026-11-16)을 따른다.

v5 대본은 **구체적 위기/행동 질문 → 관련 소개·원전 활약 → 가상 전제 → 같은 사건의 공방 1/2 → 마지막 행동·미정 질문**이다. 소개는 인물당 2 cue를 한 호흡으로 연결하며 독립 2문장이나 매번 ~요 종결을 강제하지 않는다. 같은 어미·긴 쉼·불필요한 소개를 줄인다. 원전 사건과 가상 공방을 구분하고 실제 낭독에서 소설/희곡 성격과 “~라면” 전제를 밝힌다. v4 이하의 반대 조건 전개는 해당 기존 프로필에만 유지한다.

진행 중인 호환 형식인 `schema_version: 2`의 `story_beats`에는 `id`, `kind`, `image`, `display_text`, `spoken_text`, `condition`, `header`를 순서대로 넣는다. 한 문장을 여러 자막 절로 나눌 수 있다. `narration_groups`의 `id`와 연속된 `segment_ids`로 한 호흡을 묶으면 그 cue의 낭독문을 공백으로 이어 TTS를 한 번 호출한다. 공통 CTA는 렌더러의 한 문구에서 마지막에 한 번만 추가하며 에피소드에 `cta`를 넣지 않는다. 기존 hook/intro/edge 형식도 계속 읽을 수 있다.

사용자가 두 차례 조건 공방을 요청한 v4 편은 최상위 `additional_edges`에 아래처럼 A2/B2 두 객체를 추가할 수 있다. 필드가 없는 기존 편은 그대로다. 각 title은 16자, 각 cue는 44자 이내이며 둘째 cue에서 문장을 끝낸다. 기존 A→B 뒤, 결말 앞에 A2→B2가 들어가며 기본 낭독 그룹은 `edge2_a`/`edge2_b`, cue ID는 `a.edge2.0`/`a.edge2.1`/`b.edge2.0`/`b.edge2.1`이다. 명시적 `narration_groups`가 있으면 이 cue도 순서대로 포함해야 한다. 별도 `clash` 없이 대표 a/b를 소개에 직접 쓰는 기본 편은 총 18 cue·11개 그림이 되며, 30~60초 제한과 결말·댓글 CTA는 유지한다. 이 선택 기능은 `story_beats`와 혼용하지 않는다.

```json
"additional_edges": [
  {"condition": "a", "title": "다시 거리를 벌리면", "image": "a_atk_2.png",
   "lines": ["상대의 공격을 피해 옆으로 빠져나가면,", "다시 거리를 벌릴 여지가 생깁니다."]},
  {"condition": "b", "title": "퇴로를 좁히면", "image": "b_atk_2.png",
   "lines": ["물러나는 방향을 따라 길을 좁히면,", "다시 가까이 붙을 기회를 노릴 수 있죠."]}
]
```

`asset_tools.py save`는 이미 생성된 파일을 비례 유지해 저장하고 원본·프롬프트·해시를 기록한다. 비율이 다르면 여백을 추가하고 경고한다. `review-views`는 관찰해서 지정한 얼굴/손/무기 좌표의 원본 픽셀 크롭을 만든다. 두 기능 모두 검수 PASS를 부여하지 않는다.

```powershell
.\.venv\Scripts\python.exe asset_tools.py save '생성된_원본.png' '.\episodes\001_A_vs_B\a.png' --prompt-file '실제_프롬프트.txt' --generator 'built-in image_gen'
.\.venv\Scripts\python.exe asset_tools.py review-views '.\episodes\001_A_vs_B\a.png' --regions-json '관찰한_확대영역.json'
.\.venv\Scripts\python.exe check_episode.py $episode
.\.venv\Scripts\python.exe make_voice.py $episode
.\.venv\Scripts\python.exe make_voice.py $episode --check
.\.venv\Scripts\python.exe make_vs.py $episode
```

음성 설정만 준비하려면 `make_voice.py --plan-only`를 사용한다. `voice_plan.json`의 `segments`는 cue의 발음 보정·spoken_override를, `narration_units`는 실제 생성 그룹의 voice/rate/pitch/volume/앞뒤 쉼을 관리한다. `--regenerate a.edge.0`처럼 cue ID를 지정하면 그 cue가 속한 그룹 전체를 다시 생성한다. `--regenerate group.hidden_target`처럼 실제 생성 단위 ID도 사용할 수 있다. 그룹과 구성 cue의 최초+수정 2회 한도를 함께 유지하며 그룹 이름을 바꿔 한도를 초기화하지 않는다.

묶음 내부 자막은 실제 WordBoundary 텍스트·시간과 PCM을 대응시켜 나눈다. 글자 수 비율로 타이밍을 만들지 않으며, 단어 경계가 없거나 cue가 한 단어 중간에서 갈리면 오류로 처리한다. 그룹 PCM은 한 번만 연결하고 내부에 cue별 무음을 넣지 않는다. 원본 MP3, 전체 decoded WAV, cue별 부모 sample 범위·해시, 30fps 정렬 오차를 보존한다. story 및 구조화 v3/v4/v5 경로의 저레벨 외곽 여백은 -65dBFS 기준과 신호·단어 양쪽의 150ms 보호 여백으로 보수적으로 줄이며 기존 단문 경로는 exact-zero 트림을 유지한다.

원래 대본·발음·voice/rate/pitch/volume/쉼·CTA·엔진/디코더·트림 정책이 바뀌면 해시 검증 후 영향을 받은 음성이 갱신된다. 같은 원본의 안전한 재디코딩은 TTS 재시도로 세지 않는다. 없는 음성을 무음으로 대체하지 않는다. 사용자가 무음을 명시한 경우에만 음성 생성과 합성 양쪽에 `--silent`를 붙인다.

렌더러는 최신 음성의 `timeline.json`을 사용하며 실제 음성 길이가 프로필 범위(신규 30~60초, 기존 호환 20~45초)가 아니면 실패한다. `out/video.mp4`에는 모든 문장의 자막을 직접 합성하고 같은 시간표의 `out/captions.srt`를 만든다. 명시적 무음 모드도 화면 자막은 필수다. 제목만 표시하거나 SRT만 제공하는 방식은 허용하지 않는다. 어절을 유지하는 최대 2줄 자막으로 구성하고, 필요한 경우 기존 최소 글자 크기 안에서 줄인다. 실제 MP4에서 추출한 원본 해상도 프레임, `out/_review.png`, 기술 QA도 생긴다. 콘택트시트는 탐색용이며 원본 이미지·확대 영역·최종 프레임의 직접 관찰을 대신하지 않는다.

사진에는 줌·이동·흔들림을 넣지 않는다. 사진이 실제로 바뀔 때 사진 영역만 200ms(30fps 6프레임) crossfade하고, 같은 사진에서 자막만 바뀌면 전환을 다시 시작하지 않는다. 공격→반응은 hardcut한다. 헤더·자막·테두리는 고정하며, 전환 직전·중간·직후의 최종 MP4 프레임에서 이를 검수한다.

실제 렌더 실행은 최초와 재시도/수정 2회까지 기록한다. 같은 입력으로 실패를 반복해도 횟수가 늘어난다. 동일 입력·동일 해시의 성공한 출력을 재사용할 때는 새 실행으로 세지 않는다.

## 검수와 전달

`image_review.json`, `audio_review.json`, `qa_signoffs.json`, `qa_report.md`에 실제 관찰·청취 결과와 현재 파일 해시를 기록한다. 이미지 파일 열기 성공이나 TTS 종료 성공을 시각/청취 PASS로 기록하지 않는다.

음성 검수를 시작할 때 현재 도구의 오디오 입력·분석 기능, 연결된 플러그인과 기존 로컬 음성 검사 방법을 확인한다. 첫 방법이 없다는 이유로 바로 검수대기로 넘기지 않고 실제 사용 가능한 경로를 시도한다. 원본 음성과 최종 MP4의 인물명·누락·말투·억양·속도·음량·연결부·끝 잘림·자막 일치를 확인하고 문제 구간을 수정한다. ASR은 누락과 대본 일치의 보조 검사이며 억양 청취를 대신하지 않는다. 재생 명령 성공이나 인식 텍스트만으로 `listened: true`를 기록하지 않는다.

시도한 방법과 실제 결과를 QA에 남긴다. 도구·권한·인증 제약 때문에 직접 청취와 AGENTS.md에 정의된 엄격한 로컬 모델 음향 검수 모두 충족하지 못하면 그 제한과 `AUDIO_REVIEW_REQUIRED`를 명시하고 NEEDS_REVIEW로 보존한다. 새 유료 서비스 가입·결제나 접근 우회는 하지 않는다. 환경이 바뀐 뒤 재개할 때는 청취 가능 경로를 다시 확인한다.

2026-11-16의 Gemini 합성 내레이션 검수 승인은 **2026-11-17 사용자 지시로 철회됐다**. 기존 전송·요청·오류 이력은 보존하지만 이번 및 이후 모든 용도에서 Gemini 웹·앱·API를 사용하지 않고 재승인을 반복 요청하지 않는다. 다른 외부 서비스에 대한 자동 전송 승인도 아니다. Codex 도구와 로컬 검수를 이어가며 미확인 항목을 지우거나 PASS로 바꾸지 않는다.

Qwen 승인은 별도로 **이번 `001_홍길동_vs_로빈 후드`의 합성 내레이션을 공식 Qwen3-Omni 데모에 전송하는 검수**에 한정된다. 목적지는 `https://qwen-qwen3-omni-demo.hf.space`이며 다른 편·서비스까지 승인된 것으로 보지 않는다. 기존 MP3/WAV 두 번의 요청은 업로드 후 서버 분석 오류로 끝나 실제 음향 분석을 받지 못했다. `audio_understanding_online.json`에 전송·오류 원문을 보존했고 음성 PASS로 처리하지 않았다. 같은 서버 오류나 이미 부적합한 로컬 Qwen 검수를 의미 없이 반복하지 않는다.

자막 검수는 같은 최신 `timeline.json`과 `captions.srt`의 문장·순서·시각이 일치하는지 확인하고, 실제 MP4의 모든 문장에서 표시·발화 동기화·어절 줄바꿈·잘림·겹침을 직접 확인한다. `subtitle_review.json`에 실제 본 문장별 프레임·해시·구체적 관찰을 기록하며 SRT 생성 성공만으로 화면 자막을 통과시키지 않는다. 전달 폴더에는 `captions.srt`와 `review/subtitle_review.json`, `review/timeline.json` 및 관련 검수 증거를 포함한다.

```powershell
.\.venv\Scripts\python.exe check_episode.py $episode --subtitles
.\.venv\Scripts\python.exe check_episode.py $episode --quality
```

`--subtitles`는 SRT의 문장·시간을 최신 시간표와 대조하고, `subtitle_review.json`의 실제 화면 관찰과 현재 영상·자막·프레임 해시를 검증한다. 이 검사는 `--quality`와 `--completion`에도 포함된다.

### 선택적 로컬 음성 진단

`analyze_audio.py`는 **최종 MP4의 실제 오디오**를 로컬 `faster-whisper` small 모델로 독립 전사한다. 대본·인물명·예상 단어를 힌트로 주지 않고 전체 전사를 먼저 저장한 뒤 대본과 비교한다. 기존 TTS용 `.venv`는 변경하지 않고 별도 `.asr-venv`를 사용한다.

```powershell
# .asr-venv가 없을 때만 생성
python -m venv .asr-venv
.\.asr-venv\Scripts\python.exe -m pip install -r requirements-asr.txt

# 이미 준비된 로컬 모델 폴더를 명시
.\.asr-venv\Scripts\python.exe analyze_audio.py $episode --model-dir '.\.models\faster-whisper-small'
```

현재 준비한 모델은 `Systran/faster-whisper-small`의 고정 revision `536b0662742c02347bc0e980a01041f333bce120`이다. 출처·파일 해시는 모델 폴더의 `model_source.json`에 기록되어 있다. 모델 준비는 별도 명시적 다운로드 작업이며, 분석기는 모델명만 받아 묵시적으로 다운로드하지 않는다. 실제 로컬 폴더와 `local_files_only=True`, 오프라인 모드를 사용하고 모델이 없으면 오류로 중단한다. 분석 중 오디오를 외부에 업로드하지 않는다.

결과는 편 폴더의 `audio_analysis.json`에 저장하며 `listened: false`를 유지한다. 문장 불일치·누락 의심·음량 등의 보조 진단일 뿐, ASR 오류와 실제 발음 오류를 확정적으로 구분하거나 억양의 자연스러움을 청취한 결과가 아니다. 이 도구는 `audio_review.json`을 PASS로 바꾸지 않는다. 직접 오디오 입력, 연결 도구, 공개 오디오 분석 서비스 등 실제로 시도한 경로와 제약·외부 전송 승인 상태는 해당 편의 `audio_capability_probe.json`에서 확인한다. ASR 진단이 끝나도 직접 청취 또는 별도 모델 음향 검수 게이트가 충족되지 않으면 `AUDIO_REVIEW_REQUIRED`를 유지한다.

`evaluate_audio_signal.py`는 사전에 내려받아 해시를 검증한 Microsoft DNSMOS P.835 ONNX 모델로 최종 MP4의 소리를 로컬에서 검사한다. `.asr-venv\Scripts\python.exe evaluate_audio_signal.py <episode.json>`으로 실행하며 `audio_signal_diagnostics.json`에 모델 출처, 현재 MP4/음원 해시, 끝 구간을 포함한 각 9.01초 창의 음질 추정값을 남긴다. 이 점수는 사람의 평가값이나 합격선이 아니며, 한국어 발음·억양 검수를 대신하지 않는다. 오디오 전송이나 음성 PASS 변경은 하지 않는다.

`measure_audio_prosody.py <episode.json>`은 별도 `.audio-review-venv`의 praat-parselmouth 0.4.7·SciPy·SoundFile로 원본/최종 음성의 F0, 문장 경계의 신호 간격, 인코딩 후 상관·시간 지연을 측정한다. 현재 최종 MP4의 음성을 `out/audio_analysis/final_audio_16k.wav`로 새로 추출한다. 결과는 `audio_prosody_diagnostics.json`이며 자연스러움이나 청취 PASS를 자동 부여하지 않는다. F0의 옥타브 오류, TTS 경계값의 한계, 파형 임계값과 실제 쉼의 차이를 결과에 기록한다.

로컬 음성 이해 모델은 응답의 의미까지 검토한다. 실제 음성/무음 구별만으로 한국어 억양 검수 능력이 입증되지는 않는다. 원본과 인코딩본의 모순, 별도 속도 변경 대조 실패가 있으면 `MODEL_REVIEW_INSUFFICIENT`로 남긴다. 청크 검수 증거는 실제 부모 PCM의 해당 샘플과 일치해야 하며, 모델 실행 성공·음질 점수·단순 칭찬으로 완료 상태를 바꾸지 않는다.

공식 Transformers 경로는 `.audio-transformers-venv\Scripts\python.exe run_native_audio_review.py <episode.json> --stage controls`로 대조부터 실행한다. `compare`는 정답을 숨긴 상대 속도 비교이며, `full`은 대조 결과를 실제 검토해 적합성을 확인한 뒤에만 실행한다. 모델 다운로드와 실행은 별개이며 실행기가 승인 상태를 자동으로 바꾸지 않는다. 2026-11-16 이 편에서는 Qwen2.5-Omni의 GGUF·공식 native 경로 모두 전체 음향 검수 능력을 입증하지 못했다. Native의 A/B 상대 속도 비교 성공도 문장 묶음·쉼·독립 속도 판단 오류를 해소하지 못했으므로 전체 검수는 실행하지 않았다. 동일 모델·실행기·프롬프트의 실패를 의미 없이 반복하지 않고, 새 경로를 도입할 때도 대조 검증과 실제 관찰을 먼저 확인한다. 현재 원문 증거는 해당 편의 `audio_understanding_local.json`과 `audio_understanding_native.json`에 있다.

실제 오디오 이해 모델을 사용하는 경우에도 전사만 반환하는지, 원시 음성을 입력받아 구체적인 음향 특성을 평가하는지 구분한다. 대본을 주기 전의 응답, 모델·버전, 원본 및 최종 음원 해시, 구간별 관찰을 보존한다. 파일 첨부 성공이나 일반적인 칭찬만으로 음성 검수 PASS를 부여하지 않으며, 모델 검수를 사람이 들은 결과로 보고하지 않는다.

### 최종 메타데이터와 저장

제목·설명·고정 댓글은 실제 최종 내용과 일치하는 `title.txt`, `description.txt`, `pinned_comment.txt`에 저장한다. 조건부 가상 대결, 실제 사용한 AI 이미지와 합성 음성을 설명하며 사용하지 않은 조건이나 확정 승패를 넣지 않는다.

바탕화면은 운영체제가 알려 주는 경로로 조회한다. OneDrive로 이동한 바탕화면도 이 값을 따른다.

```powershell
$desktopPath = [Environment]::GetFolderPath('Desktop')
Get-ChildItem -LiteralPath $desktopPath -Directory
```

최신 전달은 OS로 확인한 바탕화면을 `--desktop-root`로 전달해 그 바로 아래에 해시태그 포함 최종 제목과 같은 폴더를 사용한다. 경로를 추측하지 않으며 제목·title.txt·기획·보고·폴더명이 일치해야 한다. `--destination-project`는 옛 프로젝트 하위 전달의 호환 옵션이다. 목적지를 확인하지 못하면 현재 프로젝트에 보존하고 제한을 기록한다.

```powershell
# 승인 후 제작·검수·메타데이터가 준비된 결과에만 실행
.\.venv\Scripts\python.exe deliver_episode.py $episode --desktop-root $desktopPath

# 바탕화면 미확인/접근 불가: 현재 프로젝트에 검수대기로 보존
.\.venv\Scripts\python.exe deliver_episode.py $episode

# 전달 후 완료 조건 확인
.\.venv\Scripts\python.exe check_episode.py $episode --completion
```

모든 필수 검수와 전달을 마친 경우만 COMPLETE로 처리하고 queue→done을 반영한다. 최신 바탕화면 제목 폴더에 자막 합성 영상·SRT·썸네일·제목·설명·댓글 초안·출처·QA·manifest·검수 증거를 전달하며, 미확인/미해결 상태는 manifest와 보고에 남긴다. 제목 끝 네 태그는 `#가상대결 #쇼츠 #A인물 #B인물` 순서다. 임시 폴더에서 파일·해시를 확인한 뒤 최종 이름으로 확정하고 job_id로 재개한다. 과거 완성본/검수대기 경로를 신규 기본으로 사용하지 않는다. 업로드와 댓글 게시 기능은 없다.

## 검증

이번 승인 전에는 v5와 대표 v4 fixture의 **생성 없는 관련 검사**만 고른다. `check_episode.py <episode.json> --text-only`는 미디어 생성 명령이 아니지만 사실·시각·음향 검수는 별도다. dry-run/plan-only도 지원 여부와 쓰기·네트워크·생성 부작용을 먼저 확인한다. 아래 전체 회귀 명령에는 합성 시험 그림·코덱 시험 영상 경로가 포함될 수 있으므로 이번 승인 전 검증 명령으로 그대로 실행하지 않는다. 실제 수행한 검사 이름·결과·미검증 범위를 보고한다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

자동 테스트는 선택 우선순위·중복/재개·시도 상한·상태 복구·음성 무효화·반응 이미지 누락·미검수 완료 차단·타임라인/렌더 기술 검사를 확인한다. `tests/test_story_voice.py`는 그룹별 1회 연결, WordBoundary 대응 실패, 부모 PCM 재구성, 그룹/구성 cue 재시도 한도, story 트림과 명시적 무음 호환을 확인한다. 합성 시험 그림과 코덱 시험 영상은 테스트 데이터이며 실제 에피소드 이미지/음성 검수의 증거가 아니다.

신규 영상은 구체적 hook 다음에 각 인물의 단독 소개와 원전 대표 활약을 보여 준다. 첫 컷은 승인된 a.png/b.png 직접 사용 또는 a_1.png/b_1.png 별도 소개 중 하나만 사용한다. 소설·희곡·역사 기록의 구분과 공방에 필요한 특징을 자연스럽게 밝힌다. 확인되지 않은 전성기나 대표성을 만들지 않으며 상세 기준은 [v5 제작 규칙](docs/production_rules.md#showdown-detail-v5)을 따른다.
