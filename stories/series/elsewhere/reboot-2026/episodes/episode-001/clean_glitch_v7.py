#!/usr/bin/env python3
"""Targeted V7 repair: remove verified stray repeated 'you're' in V5,
without re-recording or changing other dialogue. Does NOT publish Ohana."""
import json, os
from pathlib import Path
from pydub import AudioSegment

BASE=Path(__file__).resolve().parent
OUT=Path(os.getenv("OUTPUT_DIR",BASE/"build-v7"))
OUT.mkdir(parents=True,exist_ok=True)
SOURCE=BASE/"audio"/"episode-001-v5-unreviewed.mp3"
audio=AudioSegment.from_file(SOURCE)
length_before=len(audio)
# Precise 15-second V5 audio excerpt beginning at global 200.00 sec:
# 'off' finishes at 202.16, stray 'you're' at 202.40–202.72,
# Cassian's 'Yes' begins at 203.68. Preserve both surrounding words.
# Remove ONLY the unwanted word, retain a deliberate natural pause.
start_ms=202300
end_ms=203050
assert len(audio)>1000000
guard_before=40
guard_after=40
pre=audio[start_ms-guard_before:start_ms].fade_out(guard_before)
quiet=AudioSegment.silent(duration=end_ms-start_ms,frame_rate=audio.frame_rate)
post=audio[end_ms:end_ms+guard_after].fade_in(guard_after)
fixed=audio[:start_ms-guard_before]+pre+quiet+post+audio[end_ms+guard_after:]
assert len(fixed)==length_before
output=OUT/"Elsewhere_Episode_01_Cassian_Clean_V7.mp3"
fixed.export(output,format="mp3",bitrate="96k",parameters=["-ar","44100","-ac","1"])
fixed[175000:230000].export(OUT/"around-3m18s-v7.mp3",format="mp3",bitrate="128k")
fixed[280000:330000].export(OUT/"around-5m00s-v7.mp3",format="mp3",bitrate="128k")
# The complete previous V5 re-edit was verified through independent
# transcripts for five separate sections, not a full listening check.
report={
 "version":"7-remove-repeated-youre-at-3m22",
 "source":"episode-001-v5-unreviewed.mp3",
 "source_duration_seconds":length_before/1000,
 "final_duration_seconds":len(fixed)/1000,
 "edited_time_range_ms":[start_ms,end_ms],
 "method":"replace 0.75 seconds of verified stray speech with silence; 40ms fades at edges",
 "original_word_sequence":"You're pulling my leg off. You're- Yes, that's the treatment.",
 "expected_word_sequence":"You're pulling my leg off. Yes, that's the treatment.",
 "file_size":output.stat().st_size,
 "status":"preview only; listen and transcribe before updating Ohana"
}
(OUT/"edit-report-v7.json").write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2),flush=True)
