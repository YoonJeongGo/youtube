"""Signal measurements to check, not replace, perceptual observations."""
import hashlib
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import parselmouth
import scipy
from scipy.signal import correlate, correlation_lags
import soundfile as sf

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('episode', help='episode.json path')
args = parser.parse_args()
BASE = Path(args.episode).resolve().parent
OUT = BASE / 'out/audio_audit'
OUT.mkdir(parents=True, exist_ok=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyse(path, timeline):
    data, sr = sf.read(path, dtype='float64')
    assert data.ndim == 1
    sound = parselmouth.Sound(data, sampling_frequency=sr)
    pitch = sound.to_pitch_ac(time_step=0.01, pitch_floor=65, pitch_ceiling=450)
    times = pitch.xs()
    f0 = pitch.selected_array['frequency']
    result = []
    for seg in timeline['segments']:
        start, end = seg['start'], seg['end']
        keep = (times >= start) & (times < end) & (f0 > 0)
        freq, ts = f0[keep], times[keep]
        words = [b for b in seg['boundaries'] if b['type'] == 'WordBoundary']
        last = words[-1]
        last_mask = (ts >= last['start']) & (ts <= last['end'])
        last_freq, last_ts = freq[last_mask], ts[last_mask]
        end_pitch = None
        if len(last_freq) >= 4:
            terminal_time = float(last_ts[-1])
            tail = last_freq[last_ts >= terminal_time - 0.15]
            before = last_freq[(last_ts >= terminal_time - 0.40) &
                               (last_ts < terminal_time - 0.20)]
            if len(tail) and len(before):
                end_pitch = {'last_voiced_time': terminal_time,
                             'earlier_median_hz': float(np.median(before)),
                             'terminal_median_hz': float(np.median(tail)),
                             'change_semitones': float(12 * np.log2(np.median(tail) / np.median(before)))}
        cut = data[round(start*sr):round(end*sr)]
        audible = np.flatnonzero(np.abs(cut) >= 10**(-50/20))
        syl = len(re.findall('[가-힣]', seg['spoken_text']))
        word_span = words[-1]['end'] - words[0]['start']
        result.append({'id': seg['id'], 'start': start, 'end': end,
                       'f0_voiced_frames': len(freq),
                       'f0_median_hz': float(np.median(freq)) if len(freq) else None,
                       'f0_p10_hz': float(np.percentile(freq,10)) if len(freq) else None,
                       'f0_p90_hz': float(np.percentile(freq,90)) if len(freq) else None,
                       'f0_p10_p90_semitones': float(12*np.log2(np.percentile(freq,90)/np.percentile(freq,10))) if len(freq) else None,
                       'last_word': last['text'], 'last_word_pitch': end_pitch,
                       'script_syllables_per_wordboundary_second': syl / word_span,
                       'first_sample_above_minus50dbfs_seconds': start + int(audible[0])/sr if len(audible) else None,
                       'last_sample_above_minus50dbfs_seconds': start + int(audible[-1])/sr if len(audible) else None,
                       'tail_below_minus50dbfs_seconds': (len(cut)-1-int(audible[-1]))/sr if len(audible) else len(cut)/sr})
    pauses = []
    for a, b in zip(result, result[1:]):
        pauses.append({'after': a['id'], 'before': b['id'],
                       'below_minus50dbfs_gap_seconds': b['first_sample_above_minus50dbfs_seconds']-a['last_sample_above_minus50dbfs_seconds']})
    return {'path': str(path.relative_to(BASE)), 'sha256': sha(path),
            'sample_rate': sr, 'samples': len(data), 'segments': result,
            'boundary_gaps': pauses}


timeline = json.loads((BASE/'timeline.json').read_text(encoding='utf-8'))
original16 = OUT/'original_audio_16k.wav'
subprocess.run(['ffmpeg','-v','error','-y','-i',str(BASE/'voice.wav'),'-ac','1','-ar','16000','-c:a','pcm_s16le',str(original16)], check=True)
final = BASE/'out/audio_analysis/final_audio_16k.wav'
final.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(['ffmpeg','-v','error','-y','-i',str(BASE/'out/video.mp4'),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(final)], check=True)
original = analyse(BASE/'voice.wav',timeline)
decoded = analyse(final,timeline)
x,sr=sf.read(original16)
y,ysr=sf.read(final)
assert sr==ysr==16000
corr=correlate(y,x,method='fft')
lags=correlation_lags(len(y),len(x))
bounded=np.abs(lags)<=round(sr*.2)
lag=int(lags[bounded][np.argmax(corr[bounded])])
if lag>=0:
    a,b=x[:min(len(x),len(y)-lag)],y[lag:lag+min(len(x),len(y)-lag)]
else:
    a,b=x[-lag:-lag+min(len(x)+lag,len(y))],y[:min(len(x)+lag,len(y))]
alignment={'original_resampled_sha256':sha(original16),'lag_samples':lag,
           'lag_seconds':lag/sr,'normalized_correlation':float(np.dot(a,b)/np.sqrt(np.dot(a,a)*np.dot(b,b))),
           'original_rms_dbfs':float(20*np.log10(np.sqrt(np.mean(a*a)))),
           'final_rms_dbfs':float(20*np.log10(np.sqrt(np.mean(b*b))))}
report={'schema_version':1,'status':'DIAGNOSTIC_COMPLETED_NOT_LISTENING_APPROVAL',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'source_video_sha256':sha(BASE/'out/video.mp4'),
        'timeline_sha256':sha(BASE/'timeline.json'),
        'measurement_script_sha256':sha(Path(__file__).resolve()),
        'tool':{'name':'Praat via praat-parselmouth','version':parselmouth.__version__,
                'praat_version':parselmouth.PRAAT_VERSION,'scipy_version':scipy.__version__,
                'documentation':'https://parselmouth.readthedocs.io/en/stable/api_reference.html',
                'parameters':{'method':'to_pitch_ac','time_step':.01,'pitch_floor':65,'pitch_ceiling':450}},
        'original_pcm':original,'final_mp4_audio':decoded,'encoding_alignment':alignment,
        'limitations':['F0 tracking can contain octave errors and unvoiced gaps; it is not a naturalness verdict.',
                       'Word boundary timing is from TTS, and syllable rate is calculated from script, not independent ASR.',
                       'Minus50dBFS gaps are signal thresholds, not proven linguistic pauses.',
                       'No human or Codex-native listening is claimed.'],
        'audio_uploaded':False}
target=BASE/'audio_prosody_diagnostics.json'
target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'alignment':alignment,'final_segments':decoded['segments'],'final_gaps':decoded['boundary_gaps']},ensure_ascii=False,indent=2))
