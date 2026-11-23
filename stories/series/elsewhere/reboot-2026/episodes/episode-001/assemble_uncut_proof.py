#!/usr/bin/env python3
"""One turn = one entire uncut audio file. A/B quality proof only.

Never crop TTS speech, guess word timings, splice in the middle of any WAV,
or overlay different speakers. Clips are separate HeyGen renders.
"""
from pathlib import Path
import urllib.request, json, math, os

from pydub import AudioSegment

root=Path(__file__).resolve().parent
out=Path(os.environ.get("OUTPUT_DIR",root/"audio"/"isolated-tests"))
out.mkdir(parents=True,exist_ok=True)
data=json.loads((root/"isolated-cassian-scene-test-manifest.json").read_text(encoding="utf-8"))
clips=data["clips"]
assert data["status"]=="recorded" and len(clips)==15
assert [c["run_id"] for c in clips]==list(range(21,36))
assembled=AudioSegment.silent(duration=250,frame_rate=44100)
report=[]
for c in clips:
    path=out/("clip-"+str(c["run_id"])+".wav")
    if not path.exists() or path.stat().st_size<5000:
        req=urllib.request.Request(c["audio_url"],headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=150) as source,path.open("wb") as f:
            while True:
                block=source.read(1024*1024)
                if not block:break
                f.write(block)
    take=AudioSegment.from_file(path).set_frame_rate(44100).set_channels(1)
    if abs(len(take)/1000-c["duration_seconds"])>.8:
        raise RuntimeError("Duration changed, cannot audit "+str(c["run_id"]))
    # Gain is whole-clip adjustment, not an audio edit.
    if c["gain"]!=1:take=take.apply_gain(20*math.log10(c["gain"]))
    start=len(assembled)/1000
    assembled+=take
    stop=len(assembled)/1000
    report.append({"run":c["run_id"],"speaker":c["actor"],"start":round(start,3),"end":round(stop,3),"text":c["text"]})
    assembled+=AudioSegment.silent(duration=350 if c["actor"]=="ninja" else 280,frame_rate=44100)

file=out/"cassian-15-uncut-turns-test.mp3"
assembled.export(file,format="mp3",bitrate="128k")
audit={"method":"standalone-whole-recordings-only","status":"test-not-published",
       "seconds":len(assembled)/1000,"file_size":file.stat().st_size,
       "voice_changes":sum(a["speaker"]!=b["speaker"] for a,b in zip(report,report[1:])),
       "cut_points_inside_speech":0,"stereo_overlap_count":0,
       "no_source_timing_based_edits":True,"segments":report}
(out/"audit-uncut-15.json").write_text(json.dumps(audit,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in audit.items() if k!="segments"},indent=2))
