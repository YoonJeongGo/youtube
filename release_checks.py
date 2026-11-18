"""Explicit user acceptance is a release decision, never a synthetic QA PASS."""
from pathlib import Path
from episode_core import load_json, sha256_file, script_hash, image_names


def release_errors(episode_path, quality_errors):
    base = Path(episode_path).resolve().parent
    path = base / 'user_release_acceptance.json'
    if not path.exists():
        return list(quality_errors)
    try:
        cfg = load_json(episode_path)
        acceptance = load_json(path)
        revision = acceptance['verified_revision']
        if (acceptance['job_id'] != cfg['job_id'] or not acceptance['user_statement']
                or acceptance['revised_script_hash'] != script_hash(cfg)
                or revision['status'] != 'PASS_TECHNICAL_AND_SUBTITLES'):
            raise ValueError('Acceptance does not cover the current revision')
        for name in image_names(cfg):
            if sha256_file(base / name) != acceptance['accepted_image_hashes'][name]:
                raise ValueError('Accepted image changed: ' + name)
        for name, digest in revision['files'].items():
            from episode_core import safe_child
            if sha256_file(safe_child(base, name)) != digest:
                raise ValueError('Reviewed revision changed: ' + name)
        required = {'out/video.mp4', 'timeline.json', 'voice.wav', 'out/captions.srt',
                    'audio_analysis.json', 'subtitle_review.json', 'out/technical_qa.json'}
        if not required.issubset(revision['files']):
            raise ValueError('Missing revision evidence')
        # Only these recorded perceptual limits may be accepted. Structural,
        # source, metadata, timing, decode and file errors remain blocking.
        allowed = {
            'AUDIO_REVIEW_REQUIRED: actual pronunciation/intonation listening has not passed',
            'Actual listening and specific audio observations are required',
        }
        for name in acceptance.get('accepted_image_findings', []):
            if name not in image_names(cfg):
                raise ValueError('Unknown accepted image')
            allowed.update({f'{name}: visual review is NEEDS_REVIEW',
                            f'{name}: unresolved image issues or missing issues list'})
        selected = set(acceptance.get('accepted_quality_limits', []))
        if not selected.issubset(allowed):
            raise ValueError('Unsupported acceptance scope')
        return [error for error in quality_errors if error not in selected]
    except (OSError, KeyError, ValueError, TypeError) as exc:
        return [*quality_errors, 'Invalid user release acceptance: ' + str(exc)]
