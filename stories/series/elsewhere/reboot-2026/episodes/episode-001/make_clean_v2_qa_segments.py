#!/usr/bin/env python3
"""Cut *review copies only* from completed audiobook at full-cue boundaries.
The finished master is not sliced or modified. QA chunks are <=4:30 for ASR.
"""
from pathlib import Path
from pydub import AudioSegment
import json,os
BASE=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR",BASE/"audio"/"clean-v2"/"qa"))
OUT.mkdir(parents=True,exist_ok=True)
MASTER=BASE/"audio"/"clean-phrases-v2"/"elsewhere-ep1-clean-complete-phrases-v2.mp3"
REPORT=BASE/"audio"/"clean-phrases-v2"/"clean-v2-edit-report.json"
report=json.loads(REPORT.read_text())
timeline=report["timeline"]
master=AudioSegment.from_file(MASTER).set_channels(1).set_frame_rate(44100)
assert abs(len(master)/1000-report["duration_seconds"])<.35
groups=[]
active=[]
for c in timeline:
    if active:
        if c["section"]!=active[0]["section"] or c["end"]-active[0]["start"]>235:
            groups.append(active);active=[]
    active.append(c)
if active:groups.append(active)
listed=[]
for idx,g in enumerate(groups,1):
    a=max(0,int(g[0]["start"]*1000)-80)
    b=min(len(master),int(g[-1]["end"]*1000)+80)
    if b-a>300000:raise RuntimeError("QA clip too long")
    file=OUT/("qa-"+str(idx).zfill(2)+".mp3")
    master[a:b].export(file,format="mp3",bitrate="96k")
    listed.append({"file":file.name,"scene":g[0]["section"],
                   "first_cue":g[0]["id"],"last_cue":g[-1]["id"],
                   "duration_seconds":round((b-a)/1000,3),
                   "start":a/1000,"end":b/1000,
                   "source_text":" ".join(x["text"] for x in g),
                   "speakers":sorted(set(x["actor"] for x in g)),
                   "word_count":sum(len(x["text"].split()) for x in g)})
assert all(x["duration_seconds"]<=280 for x in listed)
assert sum(x["word_count"] for x in listed)==sum(len(x["text"].split()) for x in timeline)
(OUT/"qa-cue-manifest.json").write_text(json.dumps({"source_master":"elsewhere-ep1-clean-complete-phrases-v2.mp3",
"status":"for-content-QA-only; not for playback as the master", "chunks":listed},indent=2))
print("QA SEGMENTS",json.dumps([{"file":x["file"],"scene":x["scene"],"seconds":x["duration_seconds"],"words":x["word_count"]} for x in listed],indent=2))
