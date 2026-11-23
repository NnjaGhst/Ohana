#!/usr/bin/env python3
"""Assemble Elsewhere Ep 1 in true multi-actor order.

Each voice exclusively reads lines assigned to it. Audio is split only within
the deliberately silent pauses BETWEEN complete lines from one actor; never
splice inside a word, never delete speech from the narrator master.
This creates an unreviewed branch-only preview, not a public Ohana release.
"""
from __future__ import annotations
from pathlib import Path
import json, os, urllib.request, math, statistics, hashlib
from pydub import AudioSegment

ROOT=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR",ROOT/"audio"/"cast-preview"))
OUT.mkdir(parents=True,exist_ok=True)
FILES=[
"full-cast-recordings-01-to-03.json",
"full-cast-recordings-04-to-08.json",
"full-cast-recordings-09-to-12-and-14.json",
"full-cast-recordings-cassian-ada.json",
"full-cast-recordings-16-to-20.json"
]
plan=json.loads((ROOT/"full-cast-recording-plan-v1.json").read_text(encoding="utf-8"))
records={}
for filename in FILES:
  obj=json.loads((ROOT/filename).read_text(encoding="utf-8"))
  for rec in obj.get("records",obj.get("recordings",[])):
    if rec["batch_id"] in records: raise RuntimeError(f"Duplicate batch {rec['batch_id']}")
    records[rec["batch_id"]]=rec

batches={b["batch_id"]:b for b in plan["batches"]}
expected=set(batches)
assert set(records)==expected,(sorted(expected-set(records)),sorted(set(records)-expected))
assert len(records)==20
assert plan["cast"]["ada"]["name"].startswith("Kendall")
assert plan["cast"]["cassian"]["name"].startswith("Clay")
for ident,rec in records.items():
  b=batches[ident]
  assert rec.get("audio_url"),"Audio URL missing "+ident
  # The original Wade batches 01-03 predate the explicit voice_id field.
  # Their source was verified from the same Wade plan when recorded.
  assert rec.get("voice_id",b["voice_id"])==b["voice_id"],"Unexpected voice "+ident
  assert rec.get("actor",rec.get("character"))==b["actor"]
  assert len(rec["segments"])==len(b["segments"])
  for seg,timing in zip(b["segments"],rec["segments"]):
    assert seg["run_id"]==timing["run_id"]
    assert timing.get("start_seconds",timing.get("start_s")) is not None
    assert timing.get("end_seconds",timing.get("end_s")) is not None

def download(url,tag):
  target=OUT/(tag+".wav")
  if target.exists() and target.stat().st_size>2000:return target
  req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
  with urllib.request.urlopen(req,timeout=160) as response,target.open("wb") as file:
    while True:
      data=response.read(1024*1024)
      if not data:break
      file.write(data)
  if target.stat().st_size<2000: raise RuntimeError("Bad WAV download "+tag)
  return target

def wav(url,tag):
  return AudioSegment.from_file(download(url,tag)).set_channels(1).set_frame_rate(44100)

def edge_time(prev_end,next_start,rec_id):
  delta=next_start-prev_end
  if delta<=.04:raise RuntimeError(f"Overlapping source speech {rec_id}, {delta:.3f}s")
  # Split within deliberate silence, without removing ANY of the input WAV.
  return (prev_end+next_start)/2

runs_audio={}
audits=[]
tight=[]
for batch in plan["batches"]:
  ident=batch["batch_id"]
  rec=records[ident]
  audio=wav(rec["audio_url"],ident)
  duration=len(audio)/1000
  if abs(duration-rec["duration_seconds"])>1.0:
    raise RuntimeError(f"{ident}: downloaded audio duration mismatch")
  timestamps=rec["segments"]
  cuts=[0.0]
  for i in range(1,len(timestamps)):
    previous_end=timestamps[i-1].get("end_seconds",timestamps[i-1].get("end_s"))
    next_start=timestamps[i].get("start_seconds",timestamps[i].get("start_s"))
    delta=next_start-previous_end
    if delta<.22:
      tight.append({"batch":ident,"after_run":timestamps[i-1]["run_id"],"before_run":timestamps[i]["run_id"],"speech_gap_seconds":round(delta,3)})
    cuts.append(edge_time(previous_end,next_start,ident))
  cuts.append(duration)
  for i,(segment,cue) in enumerate(zip(batch["segments"],timestamps)):
    sid=segment["run_id"]
    a=max(0,round(cuts[i]*1000));z=min(len(audio),round(cuts[i+1]*1000))
    if z-a<100:raise RuntimeError(f"Empty short clip {ident} run={sid}")
    clip=audio[a:z] # The complete source interval, including its side of silence.
    if sid in runs_audio:raise RuntimeError("Duplicate run "+str(sid))
    runs_audio[sid]=clip
    audits.append({"batch":ident,"run":sid,"actor":batch["actor"],"start":a/1000,"end":z/1000,"duration":len(clip)/1000})

# Only the five original Wade/Hallie Ada-scene test clips can be reused.
# All four earlier Joanna/Ada recordings were explicitly removed from plan.
for run in plan["runs"]:
  if "prerecorded" not in run:continue
  cue=run["prerecorded"]
  if run["character"]=="ada":raise RuntimeError("Old Joanna clip retained")
  if run["character"] not in ("narrator","ninja"):raise RuntimeError("Unapproved reused voice")
  if run["run_id"] in runs_audio:raise RuntimeError("Duplicate recording for run")
  clip=wav(cue["audio_url"],"reused-"+cue["id"])
  if abs(len(clip)/1000-cue["duration_seconds"])>.8:raise RuntimeError("Reused demo invalid "+cue["id"])
  runs_audio[run["run_id"]]=clip
  audits.append({"batch":"reused-ada-scene-demo","run":run["run_id"],"actor":run["character"],"start":0,"end":len(clip)/1000,"duration":len(clip)/1000})

all_runs=plan["runs"]
assert len(all_runs)==210
assert set(r["run_id"] for r in all_runs)==set(runs_audio)
assert all("prerecorded" not in r for r in all_runs if r["character"]=="ada")
assert all(r["voice_id"]==plan["cast"][r["character"]]["voice_id"] for r in all_runs)

mix=AudioSegment.silent(duration=180,frame_rate=44100)
timeline=[];actors={}
for ix,run in enumerate(all_runs):
  clip=runs_audio[run["run_id"]]
  if run["gain"]!=1:
    clip=clip.apply_gain(20*math.log10(run["gain"]))
  t0=len(mix)/1000
  mix+=clip
  t1=len(mix)/1000
  timeline.append({"run":run["run_id"],"actor":run["character"],"start":round(t0,3),"end":round(t1,3),"text":run["text"],"source_paragraphs":run["source_paragraphs"]})
  actors[run["character"]]=actors.get(run["character"],0)+1
  if ix+1<len(all_runs):
    nxt=all_runs[ix+1]
    if run["character"]==nxt["character"]:silent=180
    elif run["character"]=="ninja_thought" or nxt["character"]=="ninja_thought":silent=300
    else:silent=255
    mix+=AudioSegment.silent(duration=silent,frame_rate=44100)
mix+=AudioSegment.silent(duration=400,frame_rate=44100)

seconds=len(mix)/1000
if not 1200<seconds<3600:raise RuntimeError("Unexpected full episode duration "+str(seconds))
out=OUT/"episode-001-full-cast-preview.mp3"
mix.export(out,format="mp3",bitrate="96k",parameters=["-ar","44100","-ac","1"])
# A short preview to hear the first complete Ninja / Ada exchange.
mix[0:min(len(mix),230000)].export(OUT/"opening-scene-4min-preview.mp3",format="mp3",bitrate="128k")
# First Cassian scene, located from spoken line in the assembled timeline.
first_cass=next(x["start"] for x in timeline if x["actor"]=="cassian")
start=max(0,round((first_cass-18)*1000))
mix[start:min(len(mix),start+240000)].export(OUT/"cassian-first-scene-preview.mp3",format="mp3",bitrate="128k")

report={"version":"full-cast-1-actor-per-line","status":"branch-preview-not-published",
 "duration_seconds":seconds,"file_size":out.stat().st_size,
 "batch_count":len(records),"run_count":len(all_runs),
 "actor_runs":actors,"native_clips":len(audits),
 "audio_edits_inside_words":0,"overlaid_voices":0,
 "user_cast":{"cassian":"Clay – Clear & Analytical","ada":"Kendall – Soulful & Immersive"},
 "old_ada_voice_used":False,
 "ninja_volume":0.9,"ninja_thought_volume":0.8,
 "tight_source_gaps":tight,"timeline":timeline}
(OUT/"edit-report-full-cast.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print("COMPLETED",json.dumps({k:v for k,v in report.items() if k!="timeline"},indent=2),flush=True)
