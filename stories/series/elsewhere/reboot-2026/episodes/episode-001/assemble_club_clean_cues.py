#!/usr/bin/env python3
"""Elsewhere Episode 1: fix the Crooked Lantern scene with the older clean-cue workflow.

18 independently synthesized, complete speaking cues. No splitting a batch WAV
by automatic word positions. Each clip used in full. Hallie 90% (thoughts 80%);
Wade and bartender preserve pitch/speed. Natural gaps between whole cues.
Scene-specific rain and quiet pub ambiance sit UNDER speech, never edit speech.
Development preview only: do not publish to Ohana.
"""
from pathlib import Path
import json, os, urllib.request, math
import numpy as np
from scipy import signal
from pydub import AudioSegment

BASE=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR",BASE/"audio"/"clean-club"))
OUT.mkdir(parents=True,exist_ok=True)
s=json.loads((BASE/"club-standalone-cue-sheet.json").read_text())
records=[]
for file in ("club-new-recordings-01-06.json","club-new-recordings-07-12.json","club-new-recordings-13-18.json"):
    f=json.loads((BASE/file).read_text())
    assert f["status"]=="recorded"
    records.extend(f["clips"])
assert len(records)==len(s["cues"])==18
for cu,r in zip(s["cues"],records):
    for k in ("cue","run","actor","voice_id","text","gain"):
        assert cu[k]==r[k],(k,cu["cue"],r.get(k))

def get_audio(r):
    path=OUT/(r["cue"]+".source.wav")
    if not path.exists() or path.stat().st_size<3000:
        req=urllib.request.Request(r["url"],headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=150) as source,path.open("wb") as target:
            while True:
                chunk=source.read(1024*1024)
                if not chunk:break
                target.write(chunk)
    clip=AudioSegment.from_file(path).set_channels(2).set_frame_rate(44100)
    duration=len(clip)/1000
    if abs(duration-r["duration_seconds"])>.75:raise RuntimeError("Clip duration mismatch "+r["cue"])
    if not 0.40<duration<42:raise RuntimeError("Unexpected cue length "+r["cue"])
    # Apply actor gain to the COMPLETE clip. Never change speed or pitch.
    if r["gain"]!=1:clip=clip.apply_gain(20*math.log10(r["gain"]))
    return clip

voice=AudioSegment.silent(duration=180,frame_rate=44100).set_channels(2)
cue_timeline=[]
for i,r in enumerate(records):
    clip=get_audio(r)
    start=len(voice)/1000
    voice+=clip
    end=len(voice)/1000
    cue_timeline.append({"cue":r["cue"],"run":r["run"],"actor":r["actor"],"start":round(start,3),"end":round(end,3),"text":r["text"],
                         "untrimmed_source":True})
    if i+1<len(records):
        n=records[i+1]
        if r["actor"]=="narrator" and n["actor"]=="narrator":
            gap=290 # same narrator, separate full paragraphs
        elif r["actor"]=="ninja_thought" or n["actor"]=="ninja_thought":
            gap=450 # interior thoughts
        elif r["actor"]!=n["actor"]:
            gap=410 # a natural conversational handoff
        else:
            gap=330
        voice+=AudioSegment.silent(duration=gap,frame_rate=44100).set_channels(2)
voice+=AudioSegment.silent(duration=420,frame_rate=44100).set_channels(2)
voice_output=OUT/"crooked-lantern-clean-voices.mp3"
voice.export(voice_output,format="mp3",bitrate="160k")
print("VOICES COMPLETE",len(voice)/1000,"seconds",len(cue_timeline),"whole clips",flush=True)

# Compose a quiet ORIGINAL ambient bed; no third-party media used.
sr=16000
length=round(len(voice)/1000*sr)
t=np.arange(length,dtype=np.float32)/sr
rng=np.random.default_rng(4102026)
def filt(lo,hi):
    z=rng.standard_normal(length).astype(np.float32)
    sos=signal.butter(2,[lo,hi],btype="bandpass",fs=sr,output="sos")
    x=signal.sosfilt(sos,z).astype(np.float32)
    rms=np.sqrt(np.mean(np.square(x,dtype=np.float64)))
    return x/max(rms,1e-10)
def gain(db):return np.float32(10**(db/20))
vlevel=voice.dBFS
# Outdoor drizzle fades gently once Ninja pushes open the pub door.
inside=max(0, next(x["start"] for x in cue_timeline if x["cue"]=="club-02")+3)
rain_mix=np.clip((inside+4-t)/11,0,1)
pub_mix=np.clip((t-inside+4)/13,0,1)
# A little indistinct low-frequency bustle as the room comes into focus.
weather=filt(190,4400)
hush=filt(95,950)
left=(weather*rain_mix*gain(min(-45,vlevel-26))+
      hush*pub_mix*gain(min(-44,vlevel-24)))*(0.90+0.10*np.sin(.22*t))
right=(weather*rain_mix*gain(min(-47,vlevel-28))+
       hush*pub_mix*gain(min(-45,vlevel-25)))*(0.90+0.10*np.sin(.24*t+.6))
# Soften the pub at the long eye-contact scene.
eye=next(x["start"] for x in cue_timeline if x["cue"]=="club-13")
soften=np.clip((t-eye)/8,0,1)
left*=(1-.28*soften);right*=(1-.28*soften)

def sfx(time,kind,db=-42,pan=0.0):
    i=round(time*sr)
    n=round((.38 if kind=="glass" else .58)*sr)
    if i<0 or i+n>length:return
    xx=np.arange(n,dtype=np.float32)/sr
    if kind=="glass":
        w=(np.sin(2*np.pi*960*xx)+.12*np.sin(2*np.pi*1700*xx))*np.exp(-17*xx)
    else: # understated old door creak, not an alarm
        w=np.sin(2*np.pi*(122*xx+35*xx*xx))*np.sin(np.pi*xx/max(xx[-1],1e-6))**2
    w=(w/max(np.max(np.abs(w)),1e-10)*gain(db)).astype(np.float32)
    left[i:i+n]+=w*(1-pan/2)
    right[i:i+n]+=w*(1+pan/2)

door_time=next(x["start"] for x in cue_timeline if x["cue"]=="club-02")+.15
first_drink=next(x["start"] for x in cue_timeline if x["cue"]=="club-10")+1.1
second_drink=next(x["start"] for x in cue_timeline if x["cue"]=="club-12")-.3
sfx(door_time,"door",-43,-.15)
sfx(first_drink,"glass",-44,.24)
sfx(second_drink,"glass",-45,.22)

fade=np.minimum(1,t/1.8)*np.minimum(1,(len(voice)/1000-t)/3.2)
left*=fade;right*=fade
peak=float(max(np.max(np.abs(left)),np.max(np.abs(right))))
if peak>.05:
    left*=.05/peak;right*=.05/peak
    peak=.05
stereo=np.stack([left,right],axis=1)
pcm=(np.clip(stereo,-1,1)*32767).astype("<i2")
bed=AudioSegment(data=pcm.tobytes(),sample_width=2,frame_rate=sr,channels=2).set_frame_rate(44100)
bed=bed[:len(voice)]
if len(bed)<len(voice):bed+=AudioSegment.silent(duration=len(voice)-len(bed),frame_rate=44100).set_channels(2)
mixed=voice.apply_gain(-1.5).overlay(bed.apply_gain(-1.5))
if mixed.max_dBFS>-.8:mixed=mixed.apply_gain(-.8-mixed.max_dBFS)
club=OUT/"crooked-lantern-clean-with-atmosphere.mp3"
mixed.export(club,format="mp3",bitrate="160k")
# Replace the precise OLD scene boundary, NOT inside a spoken word.
original=BASE/"audio"/"dynamic-scene-soundscape"/"episode-001-dynamic-scene-soundscape-preview.mp3"
if not original.exists():raise RuntimeError("Prior complete ambient preview not available")
full=AudioSegment.from_file(original).set_channels(2).set_frame_rate(44100)
assert abs(len(full)/1000-2012.655)<.35
boundary=round(1784.75*1000) # between old run196's end (1784.62) and run197's start (1784.875)
# Existing prefix's ambience will cross into the new rain bed with no speech cut.
preview=full[:boundary]+mixed
outfile=OUT/"episode-001-club-repaired-with-scenes-preview.mp3"
preview.export(outfile,format="mp3",bitrate="160k")
report={"production":"phrase-level independent HeyGen clips, restored prior Episode 2 edit method",
        "status":"testing-only-no-Ohana-publication",
        "corrected_club_seconds":len(mixed)/1000,
        "full_episode_seconds":len(preview)/1000,
        "club_cues":len(records),"speech_cut_count":0,"speech_timestamp_based_cuts":0,
        "whole_source_files":len(records),"hallie_dialogue_gain":.9,"hallie_thought_gain":.8,
        "no_speed_or_pitch_changes":True,
        "source_episode_before_1784_75_seconds_preserved":True,
        "ambience": ["faint rain outside","quiet unintelligible pub room","door movement","two soft glass taps"],
        "ambience_peak_linear":peak,
        "club_voice_only_file_bytes":voice_output.stat().st_size,
        "club_with_ambience_file_bytes":club.stat().st_size,
        "full_episode_preview_file_bytes":outfile.stat().st_size,
        "cues":cue_timeline}
(OUT/"club-clean-rebuild-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print("CLUB REBUILD COMPLETE",json.dumps({k:v for k,v in report.items() if k!="cues"},indent=2),flush=True)
