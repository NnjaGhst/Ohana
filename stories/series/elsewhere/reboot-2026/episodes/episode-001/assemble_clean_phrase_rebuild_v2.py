#!/usr/bin/env python3
"""Rebuild Elsewhere Episode 1 using the previously successful whole-phrase approach.

All EARLIER dialogue/narration is freshly generated as one complete HeyGen take
per isolated cue. No alignment timestamps, no word slicing or overlapping voices.
The Crooked Lantern club track is re-used intact because the listener approved it.

Unpublished audio preview only. Audio must be independently checked before publication.
"""
from pathlib import Path
from pydub import AudioSegment
import json,os,math,urllib.request,hashlib,unicodedata,re
ROOT=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR",ROOT/"audio"/"clean-v2"))
OUT.mkdir(parents=True,exist_ok=True)
cue_sheet=json.loads((ROOT/"clean-rebuild-v2-cue-sheet.json").read_text(encoding="utf-8"))
assert len(cue_sheet["cues"])==248
pre=[c for c in cue_sheet["cues"] if c["run_id"]<197]
assert len(pre)==230
assert cue_sheet["cues"][:230]==pre

manifests=["001-016","017-032","033-048","049-064","065-080",
    "081-096","097-112","113-128","129-144","145-160",
    "161-176","177-192","193-208","209-224","225-230"]
recorded={}
issues=[]
for p in manifests:
    f=ROOT/("clean-rebuild-v2-recordings-"+p+".json")
    obj=json.loads(f.read_text(encoding="utf-8"))
    for rec in obj["records"]:
        name=rec["cue_id"]
        if name in recorded:issues.append("Duplicate cue: "+name)
        recorded[name]=rec

# One failed transient server response was rerecorded, and two long narrative
# passages were instead rerecorded as separate complete SENTENCES.
pickup=json.loads((ROOT/"clean-rebuild-v2-pickup-046.json").read_text())
assert pickup["status"]=="recorded" and len(pickup["records"])==1
recorded["line-046"]=pickup["records"][0]
split=json.loads((ROOT/"clean-rebuild-v2-pickups-split-lines-054-130.json").read_text())
splits={}
for v in split["records"]:
    if not v.get("url"):issues.append("Unrecorded split passage "+str(v));continue
    splits.setdefault(v["cue_id"],[]).append(v)
assert {k:len(v) for k,v in splits.items()}=={"line-054":3,"line-130":3}
for key,arr in splits.items():
    arr.sort(key=lambda x:x["subcue_index"])
    if [x["subcue_index"] for x in arr]!=[0,1,2]:issues.append("Split order "+key)
    # Plain speech does not pronounce Markdown formatting characters.
    normalize=lambda x:re.sub(r"\s+"," ",x.replace("*","")).strip()
    script=next(x["text"] for x in pre if x["id"]==key)
    if normalize(script)!=normalize(" ".join(x["source_text"] for x in arr)):
        issues.append("Script text changed by split: "+key)

assert set(recorded)==set(c["id"] for c in pre)
for cue in pre:
    rec=recorded[cue["id"]]
    if cue["id"] in splits: continue
    if not rec.get("url"):issues.append("Missing HeyGen recording "+cue["id"])
    else:
        for field,recfield in [("actor","speaker"),("voice_id","voice_id"),("gain","gain"),("text","source_text")]:
            if cue[field]!=rec.get(recfield):issues.append("Cue mismatch "+cue["id"]+" "+field)
if issues:raise RuntimeError("PRECHECK FAIL: "+"; ".join(issues))

def fetch_audio(url,tag,expected=None):
    safe=tag.replace("/","-")
    path=OUT/(safe+".source.wav")
    if not path.exists() or path.stat().st_size<3000:
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=170) as src,path.open("wb") as dest:
            while True:
                data=src.read(1024*1024)
                if not data:break
                dest.write(data)
    if path.stat().st_size<3000:raise RuntimeError("Failed WAV "+tag)
    take=AudioSegment.from_file(path).set_frame_rate(44100).set_channels(1)
    if expected is not None and abs(len(take)/1000-expected)>.85:
        raise RuntimeError("Duration mismatch "+tag)
    if len(take)<180:raise RuntimeError("Too short "+tag)
    return take

mix=AudioSegment.silent(duration=220,frame_rate=44100)
timeline=[]
last_section=None;last_actor=None
for i,cue in enumerate(pre):
    current_section=cue["section"]
    if i and last_section!=current_section:
        mix+=AudioSegment.silent(duration=660,frame_rate=44100)
    elif i:
        if cue["actor"] in ("ninja_thought",) or last_actor=="ninja_thought":gap=520
        elif cue["actor"]!=last_actor:gap=490
        elif cue["actor"]=="narrator":gap=260
        else:gap=340
        mix+=AudioSegment.silent(duration=gap,frame_rate=44100)
    start=len(mix)/1000
    pieces=[]
    if cue["id"] in splits:
        for j,item in enumerate(splits[cue["id"]]):
            piece=fetch_audio(item["url"],cue["id"]+"-part-"+str(j+1),item["duration"])
            pieces.append(piece)
    else:
        item=recorded[cue["id"]]
        pieces.append(fetch_audio(item["url"],cue["id"],item["duration"]))
    for j,clip in enumerate(pieces):
        if j:
            mix+=AudioSegment.silent(duration=230,frame_rate=44100)
        if cue["gain"]!=1:
            clip=clip.apply_gain(20*math.log10(cue["gain"]))
        mix+=clip # FULL recording. Zero trimming/crossfade/timestamp slicing.
    end=len(mix)/1000
    timeline.append({"id":cue["id"],"run_id":cue["run_id"],"actor":cue["actor"],
        "section":cue["section"],"start":round(start,3),"end":round(end,3),
        "text":cue["text"],"source_paragraphs":cue["source_paragraphs"],
        "part_count":len(pieces)})
    last_section=current_section;last_actor=cue["actor"]

pre_club_end=len(mix)/1000
mix+=AudioSegment.silent(duration=680,frame_rate=44100)
club_path=ROOT/"audio"/"club-repaired-preview"/"crooked-lantern-clean-voices.mp3"
if not club_path.is_file():raise RuntimeError("Approved club file unavailable")
club=AudioSegment.from_file(club_path).set_channels(1).set_frame_rate(44100)
if not 225<len(club)/1000<245:raise RuntimeError("Unrecognized club master")
club_start=len(mix)/1000
mix+=club
duration=len(mix)/1000
assert 1500<duration<2900,("Unexpected duration",duration)

# Append the previously approved bar's detailed timeline as an *absolute* cue
# map. It is already made from 18 independent complete recordings.
club_report=json.loads((ROOT/"audio"/"club-repaired-preview"/"club-clean-rebuild-report.json").read_text())
for c in club_report["cues"]:
    timeline.append({"id":c["cue"],"run_id":c["run"],"actor":c["actor"],
        "section":"The Crooked Lantern","start":round(club_start+c["start"],3),
        "end":round(club_start+c["end"],3),"text":c["text"],"source_paragraphs":[],"part_count":1})

if len(timeline)!=248:raise RuntimeError("Cue omission")
expected_ids=[c["id"] for c in pre]
actual_ids=[x["id"] for x in timeline[:230]]
assert expected_ids==actual_ids
all_subclips=sum(x["part_count"] for x in timeline)
assert all_subclips==252 # 230 preclub + 4 extra split parts + 18 club
# Keep relative acting/volume; apply uniform tiny headroom if needed.
if mix.max_dBFS>-.8:
    mix=mix.apply_gain(-.8-mix.max_dBFS)
name=OUT/"elsewhere-ep1-clean-complete-phrases-v2.mp3"
mix.export(name,format="mp3",bitrate="160k",parameters=["-ar","44100","-ac","1"])
def part(label,start,end):
    f=OUT/(label+".mp3")
    mix[round(start*1000):round(min(end,duration)*1000)].export(f,format="mp3",bitrate="128k")
    return {"file":f.name,"start":round(start,2),"end":round(min(end,duration),2),"bytes":f.stat().st_size}

bySection={}
for t in timeline:
    sec=bySection.setdefault(t["section"],{"start":t["start"],"end":t["end"],"count":0})
    sec["start"]=min(sec["start"],t["start"]);sec["end"]=max(sec["end"],t["end"]);sec["count"]+=1

previews=[
 part("opening-and-childhood-clean",0,min(390,duration)),
 part("last-evening-and-news-clean",next(x["start"] for x in timeline if x["section"]=="The Last Ordinary Evening"),next(x["start"] for x in timeline if x["section"]=="A House Full of Missing Things")+38),
 part("crooked-lantern-clean",club_start,duration)
]
report={"status":"unpublished-qa-preview","method":"fresh full uncut per-phrase TTS, previously approved club",
 "dialogue_cuts":0,"timestamp_cuts":0,"source_clip_cropping":0,
 "audio_change_within_words":0,"pre_club_cues":230,"pre_club_recording_count":234,
 "existing_club_unmodified":True,"club_cues":18,"duration_seconds":duration,
 "full_preview_bytes":name.stat().st_size,"club_start_seconds":club_start,
 "previous_sliced_master_reused":False,
 "cast":{"narrator":"Wade","ninja":"Hallie","cassian":"Clay","ada":"Kendall"},
 "ninja_dialogue_gain":.9,"ninja_thought_gain":.8,
 "scenes":bySection,"previews":previews,"timeline":timeline}
(OUT/"clean-v2-edit-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print("UNTRIMMED REBUILD COMPLETE",json.dumps({k:v for k,v in report.items() if k!="timeline"},indent=2),flush=True)
