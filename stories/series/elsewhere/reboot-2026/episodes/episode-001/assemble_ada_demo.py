#!/usr/bin/env python3
"""Mix first complete cast-separated encounter with Ada.
Each complete TTS clip is concatenated; ZERO cuts, replacements or overlays.
"""
from pathlib import Path
from pydub import AudioSegment
import urllib.request, json, math
BASE=Path(__file__).parent
C=json.loads((BASE/"ada-first-meeting-voice-test.json").read_text())
assert C["status"]=="recorded" and len(C["cues"])==9
OUT=BASE/"audio"/"full-cast-previews"
OUT.mkdir(parents=True,exist_ok=True)
mix=AudioSegment.silent(duration=175,frame_rate=44100)
stats=[]
for i,cue in enumerate(C["cues"]):
    source=OUT/f"{cue['id']}.wav"
    if not source.exists() or source.stat().st_size<5000:
        req=urllib.request.Request(cue["audio_url"],headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=120) as response,source.open("wb") as fh:
            while data:=response.read(1024*1024):fh.write(data)
    clip=AudioSegment.from_file(source).set_frame_rate(44100).set_channels(1)
    if not 400<=len(clip)<=25000:raise RuntimeError("Invalid independent line clip "+cue["id"])
    if abs(len(clip)/1000-cue["duration_seconds"])>.9:raise RuntimeError("Duration mismatch "+cue["id"])
    gain=cue["gain"]
    if gain!=1:clip=clip.apply_gain(20*math.log10(gain))
    stats.append({"id":cue["id"],"speaker":cue["character"],"source_seconds":len(clip)/1000,"starting_at_seconds":len(mix)/1000})
    mix+=clip
    # Spoken turns have actual pauses; no cutting, fading, or overlapping.
    mix+=AudioSegment.silent(duration=300 if cue["character"] in ("ada","ninja") else 230,frame_rate=44100)
if len(stats)!=9:raise RuntimeError("Missing dialogue clips")
name=OUT/"ada-first-meeting-clean-voices-preview.mp3"
mix.export(name,format="mp3",bitrate="128k")
report={"source":"full-cast-script-v1.json","kind":"full-cast-voice-quality-demo","duration_seconds":len(mix)/1000,"parts":stats,"max_simultaneous_speakers":1,"overlap_count":0,"cut_points_inside_spoken_lines":0,"pitch_modified":False,"hallie_gain":0.9,"status":"preview-only; not published in Ohana"}
(OUT/"ada-scene-report.json").write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2),flush=True)
