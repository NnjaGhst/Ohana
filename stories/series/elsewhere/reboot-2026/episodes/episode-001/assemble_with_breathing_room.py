#!/usr/bin/env python3
"""Listener A/B test: keep each complete voice recording untouched and let lines breathe.

No timestamp cuts, in-word edits, fades, crossfades, overlays or pitch changes.
This is a BRANCH-ONLY test, and does not modify the published Ohana episode.
"""
import json, urllib.request, math, os
from pathlib import Path
from pydub import AudioSegment

ROOT=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR", ROOT/"audio"/"isolated-tests"))
OUT.mkdir(parents=True, exist_ok=True)
recordings=json.loads((ROOT/"isolated-cassian-scene-test-manifest.json").read_text())["clips"]
corrected=json.loads((ROOT/"isolated-hallie-line-26-correction.json").read_text())
assert len(recordings)==15 and corrected["run_id"]==26

def get_audio(url, name, expected_duration):
    file=OUT/name
    if not file.exists() or file.stat().st_size<5000:
        request=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(request,timeout=150) as source, file.open("wb") as dest:
            while True:
                data=source.read(1024*1024)
                if not data: break
                dest.write(data)
    take=AudioSegment.from_file(file).set_channels(1).set_frame_rate(44100)
    if abs(len(take)/1000-expected_duration)>.8:
        raise RuntimeError("Unexpected source duration for "+name)
    return take

# The silence belongs *between complete speech turns*, not inside words.
# Previous corrected test only inserted 280/350ms after each turn.
# Give an extra 300–350ms for the breath / voice handoff.
LEAD_MS=200
GAP_AFTER_NARRATION_MS=570
GAP_AFTER_DIALOGUE_MS=700
TAIL_MS=450
mix=AudioSegment.silent(duration=LEAD_MS,frame_rate=44100)
timeline=[]
for index,rec in enumerate(recordings):
    if rec["run_id"]==26:
        take=get_audio(corrected["audio_url"],"hallie-26-corrected-untrimmed.wav",corrected["duration_seconds"])
    else:
        take=get_audio(rec["audio_url"],"original-untrimmed-"+str(rec["run_id"])+".wav",rec["duration_seconds"])
    if rec["gain"]!=1:
        take=take.apply_gain(20*math.log10(rec["gain"]))
    start=len(mix)
    mix+=take
    end=len(mix)
    timeline.append({"run_id":rec["run_id"],"speaker":rec["actor"],"start_ms":start,
                     "end_ms":end,"text":rec["text"],"source_untrimmed":True})
    if index<len(recordings)-1:
        ms=GAP_AFTER_NARRATION_MS if rec["actor"]=="narrator" else GAP_AFTER_DIALOGUE_MS
        mix+=AudioSegment.silent(duration=ms,frame_rate=44100)
mix+=AudioSegment.silent(duration=TAIL_MS,frame_rate=44100)
file=OUT/"cassian-15-uncut-turns-more-breathing-room.mp3"
mix.export(file,format="mp3",bitrate="128k",parameters=["-ar","44100","-ac","1"])
report={
"kind":"breathing-room-test-not-published","duration_seconds":len(mix)/1000,
"word_cuts":0,"trimmed_voice_clips":0,"crossfades":0,
"narration_gap_ms":GAP_AFTER_NARRATION_MS,"dialogue_gap_ms":GAP_AFTER_DIALOGUE_MS,
"source_voice":"Clay for Cassian, Hallie for Ninja, Wade narrator",
"correction":"Verified clean alternate Hallie line 26",
"runs":timeline,"file_bytes":file.stat().st_size}
(OUT/"breathing-room-test-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print("BREATHING ROOM TEST",json.dumps({k:v for k,v in report.items() if k!="runs"},indent=2),flush=True)
