#!/usr/bin/env python3
"""Elsewhere E1: scene-by-scene ORIGINAL cinematic ambience under unmodified speech.
No licensed samples, no synthetic intelligible background conversation, no music bed.
No word cuts, no time stretching, no joins added to the existing voice preview.
"""
from pathlib import Path
import json,math,os
import numpy as np
from scipy import signal
from pydub import AudioSegment

BASE=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR",BASE/"audio"/"dynamic-ambience"))
OUT.mkdir(parents=True,exist_ok=True)
config=json.loads((BASE/"clean-v2-retimed-soundscape.json").read_text())
voice_path=BASE/"audio"/"clean-phrases-v2"/"elsewhere-ep1-clean-complete-phrases-v2.mp3"
voice=AudioSegment.from_file(voice_path).set_channels(2).set_frame_rate(44100)
vlen=len(voice)
seconds=vlen/1000
assert abs(seconds-config["duration"])<.3,(seconds,config["duration"])
sr=16000
N=round(seconds*sr)
L=np.zeros(N,dtype=np.float32)
R=np.zeros(N,dtype=np.float32)
rng=np.random.default_rng(20261124)
env_descriptions={
"wet_street":"soft band-passed rain, distant street rumble",
"bakery_room":"muffled exterior rain, warm quiet indoor air",
"courtyard":"open-air soft breeze and residential hush",
"childhood_home":"warm room tone, low outdoor sounds through walls",
"evening_street":"restrained evening street and distance traffic",
"uneasy_room":"quiet enclosed room without artificial tension drones",
"warm_living_room":"low domestic atmosphere, no copyrighted songs",
"doorstep_quiet":"almost silent interior, space for emotional dialogue",
"grief_room":"understated near-silence, no added crying",
"empty_apartment":"quiet air with far traffic through windows",
"departure":"gentle roadside breeze and distant passing air",
"pub":"muted, nonverbal pub bustle, gentle room tone"
}
def amp(db):
    return np.float32(10.0**(db/20.0))
def filtered_noise(n,lo,hi):
    z=rng.standard_normal(n).astype(np.float32)
    sos=signal.butter(2,[lo,hi],btype="bandpass",fs=sr,output="sos")
    return signal.sosfilt(sos,z).astype(np.float32)
def power(x):
    return float(np.sqrt(np.mean(np.square(x,dtype=np.float64))))
def make_scene(scene,start,end):
    n=round((end-start)*sr)
    tt=np.arange(n,dtype=np.float32)/sr
    kind=scene["texture"]
    if kind=="wet_street":
        a=filtered_noise(n,170,4700);b=filtered_noise(n,70,290)
        x=a*.8+b*.32
    elif kind=="bakery_room":
        x=filtered_noise(n,90,650)*.78+filtered_noise(n,100,280)*.24
    elif kind=="courtyard":
        x=filtered_noise(n,170,1200)*.72+filtered_noise(n,90,380)*.3
    elif kind in ("childhood_home","warm_living_room"):
        x=filtered_noise(n,90,490)*.76+filtered_noise(n,65,220)*.24
    elif kind in ("evening_street","departure"):
        x=filtered_noise(n,95,1000)*.7+filtered_noise(n,55,280)*.35
    elif kind in ("uneasy_room","doorstep_quiet","grief_room","empty_apartment"):
        x=filtered_noise(n,75,420)*.85+filtered_noise(n,120,650)*.16
    elif kind=="pub":
        # Soft indistinct chatter-like texture with no intelligible voices.
        x=filtered_noise(n,180,1150)*.7+filtered_noise(n,100,350)*.24
    else: raise RuntimeError("Unknown environment "+kind)
    slow=0.89+0.08*np.sin(tt*.33)+0.03*np.sin(tt*.73+1.4)
    if kind=="pub":
        slow*=0.86+0.13*np.sin(tt*.98)
    if kind in ("wet_street","evening_street"):
        slow*=0.91+0.09*np.sin(tt*.49)
    x*=slow
    x=x/max(power(x),1e-8)*amp(scene["intensity"])
    # Distribute sound softly in stereo with decorrelated but related space.
    y=0.85*x+0.15*filtered_noise(n,160,2200)
    y=y/max(power(y),1e-8)*amp(scene["intensity"]-1.3)
    return x,y

fade_s=config["sceneCrossfadeSeconds"]
scenes=config["scenes"]
assert len(scenes)==13
for i,scene in enumerate(scenes):
    start=max(0.0,scene["from"]-fade_s)
    end=min(seconds,scene["to"]+fade_s)
    x,y=make_scene(scene,start,end)
    t=np.arange(len(x),dtype=np.float32)/sr+start
    # Curved overlapping scene fades. No sudden silence / abrupt switch.
    if i==0:
        fadein=np.minimum(1.0,t/2.5)
    else:
        fadein=np.clip((t-(scene["from"]-fade_s))/(2*fade_s),0,1)
    if i==len(scenes)-1:
        fadeout=np.minimum(1.0,(seconds-t)/8.0)
    else:
        fadeout=np.clip(((scene["to"]+fade_s)-t)/(2*fade_s),0,1)
    envelope=fadein*fadeout
    first=int(round(start*sr))
    upper=min(N,first+len(x))
    used=upper-first
    L[first:upper]+=(x[:used]*envelope[:used]).astype(np.float32)
    R[first:upper]+=(y[:used]*envelope[:used]).astype(np.float32)
    print(f"SCENE {i+1:02d}: {scene['name']} {scene['from']:.1f}-{scene['to']:.1f} {scene['intensity']} dBFS",flush=True)

def add_wave(time, waveform, gain_db=-44, pan=0.0):
    k=round(time*sr)
    if k<0 or k>=N:return
    z=np.asarray(waveform,dtype=np.float32)
    z=z[:min(len(z),N-k)]
    if len(z)<4:return
    scale=amp(gain_db)/max(np.max(np.abs(z)),1e-10)
    l=np.sqrt((1-pan)/2)*1.414
    r=np.sqrt((1+pan)/2)*1.414
    L[k:k+len(z)]+=z*scale*l
    R[k:k+len(z)]+=z*scale*r

def wav_noise(dur,low=300,high=2800):
    n=max(5,round(dur*sr))
    z=rng.standard_normal(n).astype(np.float32)
    sos=signal.butter(2,[low,high],btype="bandpass",fs=sr,output="sos")
    z=signal.sosfilt(sos,z).astype(np.float32)
    t=np.arange(n,dtype=np.float32)/n
    env=np.sin(np.pi*t)**2
    return z*env

def tone(dur,freq,decay=6):
    n=round(dur*sr)
    t=np.arange(n,dtype=np.float32)/sr
    # Acoustic-sounding softened bell or room sound, no harsh upper frequencies.
    env=np.exp(-decay*t/dur)*(1-np.exp(-t*80))
    return (np.sin(2*np.pi*freq*t)+.14*np.sin(2*np.pi*freq*2.03*t))*env

def sound_event(event):
    when=event["time"]
    typ=event["type"]
    level=event["level"]
    if typ in ("cloth","paper"):
        z=wav_noise(.55 if typ=="paper" else .35,280,2800)
        add_wave(when,z,level,pan=-.25)
        if typ=="paper":add_wave(when+.32,wav_noise(.28,450,3000),level-5,pan=.12)
    elif typ=="water":
        for offset,delta in [(0,0),(.12,-5),(.26,-7),(.37,-4)]:
            add_wave(when+offset,wav_noise(.20,700,4700),level+delta,pan=-.35)
    elif typ in ("glass","plate","ceramic"):
        f={"glass":1060,"plate":640,"ceramic":540}[typ]
        add_wave(when,tone(.55,f,decay=6.3),level,pan=.3)
        add_wave(when+.06,wav_noise(.13,900,3800),level-9,pan=.18)
    elif typ in ("click","keys"):
        add_wave(when,wav_noise(.13,700,4100),level,pan=.25)
        if typ=="keys":
            add_wave(when+.18,tone(.26,1150),level-5,pan=.38)
            add_wave(when+.33,tone(.23,1450),level-8,pan=.38)
    elif typ=="door":
        add_wave(when,tone(.35,128,decay=4.3),level-3,pan=-.3)
        add_wave(when+.31,wav_noise(.11,450,2400),level,pan=-.2)
    elif typ=="creak":
        n=int(.52*sr);t=np.arange(n)/sr
        wave=np.sin(2*np.pi*(135*t+60*t*t))*(np.sin(np.pi*t/.52)**1.5)
        add_wave(when,wave.astype(np.float32),level,pan=-.1)
    elif typ=="steps":
        for i in range(4):
            add_wave(when+i*.42,wav_noise(.16,90,600),level-3,pan=(-.2 if i%2 else .2))
    elif typ=="knock":
        for offset in (0,.37,.78):
            z=tone(.22,120,decay=8)+.32*wav_noise(.22,140,1600)
            add_wave(when+offset,z,level-2,pan=-.3)
    elif typ=="car":
        n=int(3.3*sr);t=np.arange(n)/sr
        w=rng.standard_normal(n).astype(np.float32)
        so=signal.butter(2,[55,250],btype="bandpass",fs=sr,output="sos")
        filtered=signal.sosfilt(so,w).astype(np.float32)
        env=np.sin(np.pi*t/3.3)**1.5
        add_wave(when,filtered*env,level,pan=.1)
    else:raise RuntimeError("Unknown foley "+typ)
for e in config["events"]:
    sound_event(e)
# Very short ORIGINAL, quiet two-note transitions, not a repeated soundtrack.
for marker in config["attentionMotifs"]:
    t0=marker["time"]+1.25
    if marker["kind"]=="memory":notes=[392.0,493.9]
    elif marker["kind"]=="warm":notes=[349.2,440.]
    else:notes=[440.,523.25]
    for j,f in enumerate(notes):
        add_wave(t0+j*.42,tone(.92,f,decay=5),-46-j*2,pan=(-.22 if j==0 else .17))
# Use a faint tail near the last eye contact, not an intrusive dramatic sting.
# Limit ambience itself to 0.05 peak linear, preventing level spikes.
p=max(float(np.max(np.abs(L))),float(np.max(np.abs(R))))
if p>.05:
    scalar=.05/p
    L*=scalar;R*=scalar
    p=.05
pcm=np.stack([L,R],axis=1)
del L,R
np.clip(pcm,-1.0,1.0,out=pcm)
audio_bytes=(pcm*32767).astype("<i2").tobytes()
del pcm
bed=AudioSegment(data=audio_bytes,sample_width=2,frame_rate=sr,channels=2).set_frame_rate(44100)
del audio_bytes
bed=bed[:vlen]
if len(bed)<vlen:bed+=AudioSegment.silent(duration=vlen-len(bed),frame_rate=44100).set_channels(2)
assert len(voice)==len(bed)
# Small global headroom, applied to voices and ambience together.
# Relative Wade/Hallie/Clay/Kendall levels and timing remain unchanged.
mixed=voice.apply_gain(-1.6).overlay(bed.apply_gain(-1.6))
peak_db=mixed.max_dBFS
if peak_db>-.9:
    mixed=mixed.apply_gain(-.9-peak_db)
outfile=OUT/"episode-001-clean-v2-with-scenes.mp3"
mixed.export(outfile,format="mp3",bitrate="160k")
previews=[(v["label"],v["start"],v["end"]) for v in config["preview_ranges"]]
for label,start,end in previews:
    segment=mixed[round(start*1000):round(end*1000)]
    segment.export(OUT/(label+".mp3"),format="mp3",bitrate="128k")
report={"mix":"scene-responsive-clean-phrases-v2","status":"preview-only; do not publish without user approval",
        "base_voice_audio_unchanged_in_timing_and_words":True,
        "caveat":"New whole-phrase voice master has passed chapter-by-chapter transcription review; listener should still inspect naturalness and background mix.",
        "duration_seconds":len(mixed)/1000,
        "file_size":outfile.stat().st_size,
        "scene_count":len(scenes),"foley_cue_count":len(config["events"]),"motif_count":len(config["attentionMotifs"]),
        "ambience_peak_linear_before_mastering":p,
        "output_peak_dbfs":mixed.max_dBFS,
        "voices_mixed_headroom_gain_db":-1.6,
        "sound_source":"original generated synthesis (no external licensed files)",
        "scenes":scenes,"foley":config["events"],
        "preview_clips":[{"file":label+".mp3","start":start,"end":end} for label,start,end in previews]}
(OUT/"clean-v2-soundscape-edit-report.json").write_text(json.dumps(report,indent=2))
print("DYNAMIC AUDIO MIX COMPLETE",json.dumps({k:v for k,v in report.items() if k not in ("scenes","foley")},indent=2),flush=True)
