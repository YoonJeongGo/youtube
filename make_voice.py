"""Voice CLI compatibility entrypoint; immutable generation plus opt-in pacing."""
import sys
import voice_engine_v2 as _engine
from voice_pacing import install

install(_engine)

if __name__ == '__main__':
    raise SystemExit(_engine.cli())
else:
    # A real module alias keeps existing patch(make_voice.fetch_tts/engine_info)
    # contracts pointed at the same globals used by generation functions.
    sys.modules[__name__] = _engine
