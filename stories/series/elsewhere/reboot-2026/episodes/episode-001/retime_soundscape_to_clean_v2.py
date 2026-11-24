#!/usr/bin/env python3
"""Rebase the scene-specific sound cues onto the newly rerecorded episode timeline.

Old durations are NOT reused. Source scene times are mapped through the
matching original run ID to the new full-phrase recording's timing map.
"""
import json
from pathlib import Path
BASE=Path(__file__).resolve().parent
old=json.loads((BASE/"audio"/"full-cast-previews"/"edit-report-full-cast.json").read_text())
new=json.loads((BASE/"audio"/"clean-phrases-v2"/"clean-v2-edit-report.json").read_text())
config=json.loads((BASE/"dynamic-ambience-cue-sheet.json").read_text())
ot=old["timeline"]
nt=new["timeline"]
index={}
for r in nt:
    x=index.setdefault(r["run_id"],{"start":r["start"],"end":r["end"]})
    x["start"]=min(x["start"],r["start"])
    x["end"]=max(x["end"],r["end"])
assert len(index)==210
def mapped(t):
    if t<=0:return 0.0
    if t>=old["duration_seconds"]:return new["duration_seconds"]
    for i,r in enumerate(ot):
        if t<r["start"]:
            if i==0:return max(0,t)
            prev=ot[i-1]
            a=index[prev["run"]]["end"]
            b=index[r["run"]]["start"]
            delta=(t-prev["end"])/max(.001,r["start"]-prev["end"])
            return round(a+max(0,min(1,delta))*(b-a),4)
        if r["start"]<=t<=r["end"]:
            n=index[r["run"]]
            fraction=(t-r["start"])/max(.01,r["end"]-r["start"])
            return round(n["start"]+fraction*(n["end"]-n["start"]),4)
    return new["duration_seconds"]
out=dict(config)
out["source_profile"]="dynamic-ambience-cue-sheet.json"
out["mapping_provenance"]="run-level linear correspondence old full-cast to newly rerecorded full-phrase timeline"
out["source_voice_file"]="audio/clean-phrases-v2/elsewhere-ep1-clean-complete-phrases-v2.mp3"
out["status"]="preview-needs-listening; never publish without user approval"
out["duration"]=new["duration_seconds"]
out["scenes"]=[dict(s,**{"from":mapped(s["from"]),"to":mapped(s["to"])}) for s in config["scenes"]]
out["events"]=[dict(v,time=mapped(v["time"])) for v in config["events"]]
out["attentionMotifs"]=[dict(v,time=mapped(v["time"])) for v in config["attentionMotifs"]]
out["preview_ranges"]=[
{"label":"rain-bakery-courtyard-clean","start":mapped(22),"end":mapped(224)},
{"label":"last-evening-knock-clean","start":mapped(850),"end":mapped(1120)},
{"label":"return-bellwether-pub-clean","start":mapped(1757),"end":new["duration_seconds"]}
]
out["scenes"][0]["from"]=0
out["scenes"][-1]["to"]=new["duration_seconds"]
for i,s in enumerate(out["scenes"]):
    if not s["to"]>s["from"]:raise RuntimeError("Nonpositive scene "+s["name"])
    if i and out["scenes"][i-1]["to"]>s["from"]+1:raise RuntimeError("Overlapping scene order")
for v in out["events"]:
    if v["time"]<0 or v["time"]>out["duration"]:raise RuntimeError("Event outside track")
path=BASE/"clean-v2-retimed-soundscape.json"
path.write_text(json.dumps(out,indent=2),encoding="utf-8")
print("RETIMED",len(out["scenes"]),"scenes",len(out["events"]),"events to",out["duration"],"seconds")
