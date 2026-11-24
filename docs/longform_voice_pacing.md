# 롱폼 음성 호흡 편집 실행

신규 롱폼은 대본 승인 후 에피소드 폴더에 `voice_pacing_plan.json`을 함께 준비한다. 아래 정책 파일이 있고 `enabled: true`이면 일반 `make_voice.py` 생성 및 `--check` 명령에 파형 편집과 재구성 검증이 자동 연결된다. 파일이 없는 기존 롱폼과 쇼츠의 음성은 자동 변경하지 않는다.

```json
{
  "schema_version": 1,
  "enabled": true,
  "sentence_early_seconds": 0.4,
  "sentence_late_seconds": 0.4,
  "paragraph_seconds": 0.7,
  "chapter_seconds": 1.2,
  "early_scene_count": 7,
  "quiet_rms_dbfs": -45,
  "max_removed_abs_sample": 48,
  "quiet_edge_guard_seconds": 0.05
}
```

위의 양쪽 문장 쉼 0.4초가 공통 운영 기본값이다. 값이 같으므로 장 수와 관계없이 모든 장에 0.4초를 적용한다. 공통 허용 범위는 문장 0.3~0.5초, 문단 0.7초, 장 1.2초다.

승인된 이번 B안만 `sentence_early_seconds: 0.3`, `sentence_late_seconds: 0.4`로 앞 7장과 그 뒤를 구분한다. 실제 B 정책 파일은 이 값 그대로 유지한다. 현재 구현의 `early_scene_count`는 7을 검증하므로 서로 다른 두 값을 다른 장 경계에서 나누려면 명시적인 구현 변경과 검증이 필요하다. 이 구현 제약을 모든 작품의 장 구성 규칙으로 확대하지 않는다.

```powershell
# D:\유튜브 자동 쇼츠 생성 에서 실행
.\run_python.ps1 make_voice.py '<에피소드>\episode.json'
.\run_python.ps1 make_voice.py '<에피소드>\episode.json' --check
```

`make_voice.py`는 기존 생성 구현인 `voice_engine_v2.py` 모듈을 그대로 재노출한다. 원본 생성 파일의 바이트와 생성 해시를 보존하며, 기존 함수의 mock/patch 대상도 같은 모듈이다. 파형 편집은 `voice_pacing.py`가 수행한다. 정책/편집기 해시는 통합 음성과 시간표의 입력에 포함되고, TTS 소스 캐시 해시에는 포함되지 않는다. 정책 변경만으로 동일 대본의 TTS를 다시 요청하거나 생성 횟수를 초기화하지 않는다.

문장 경계는 승인된 원문의 종결 부호를 실제 WordBoundary에 대응시킨다. 낭독 그룹 사이는 문단, 씬 변경은 장 경계로 해석하며 장 간격이 문단 간격보다 우선한다. 자막 cue 수에 맞춰 쉼을 넣지 않는다. 합친 낭독 그룹이 편집상 문단과 다르면 생성 전 그룹 구성을 확인한다.

편집은 10ms RMS 창으로 측정한 -45dBFS 이하 구간 안에서만 수행한다. 양쪽 50ms를 보호하고, 제거 가능한 샘플의 절대 진폭을 48/32768(-56.68dBFS) 이하로 제한한다. 실제 발화나 말끝을 보호하느라 목표보다 쉼이 길어지면 그대로 남겨 명세에 기록한다. 모든 원본 MP3, 디코딩 PCM, WordBoundary와 생성 횟수는 유지한다. 배속·대본 삭제·최저 길이를 맞추기 위한 패딩은 하지 않는다.

WordBoundary 원본 ticks/샘플을 보존한 채 동일 샘플 변환으로 새 단어·자막 시간을 계산한다. 제거된 저레벨 무음 내부의 시각은 해당 단어의 보존된 시작/끝 쪽으로 연결한다. 글자 수 비율로 발화 시각을 만들지 않는다. 30fps 경계가 단어 사이에 들어가지 않으면 해당 자막만 본문 그대로 병합해야 하며 음성을 잘라 맞추지 않는다.

완료된 `timeline.json`에는 `pacing_applied`, `pacing_manifest_file`, `pacing_manifest_sha256`, `pcm_sha256`이 있다. `voice_pacing_manifest.json`은 원본/최종 PCM 해시, 각 편집의 원본 샘플 범위·최대 진폭·대체 길이·실제 쉼·보호 때문에 늘어난 구간을 기록한다. `--check`는 보존된 부모 PCM에서 최종 PCM과 모든 단어·자막·그룹 경계·명세를 다시 계산해 일치 여부를 검사한다.

최종 음성 판단은 별도다. 실제 MP4 AAC의 전체 ASR, 신호·끝 잘림·음량·자막 정합을 검사해도 사용자가 전체 영상을 듣고 승인하기 전에는 `AUDIO_REVIEW_REQUIRED`/청취 승인 대기를 유지한다.
