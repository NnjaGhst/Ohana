#!/usr/bin/env python3
"""Original, very quiet courtyard ambience for the user-approved dialogue test.
No changes to spoken recordings, no licensed sound effects."""
import json,math,os
from pathlib import Path
import numpy as np
from scipy import signal
from pydub import AudioSegment
BASE=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR",BASE/"audio"/"ambient-tests"))
OUT.mkdir(parents=True,exist_ok=True)
SOURCE=BASE/"audio"/"isolated-tests"/"cassian-dialogue-quiet-edges-a-b-test.mp3"
voice=AudioSegment.from_file(SOURCE).set_channels(2).set_frame_rate(44100)
assert 57<len(voice)/1000<64
sr=22050
N=round(len(voice)/1000*sr)
t=np.arange(N,dtype=np.float32)/sr
rng=np.random.default_rng(161122)
voice_rms=voice.dBFS
def amp(db):return np.float32(10**(db/20))
def noise(lo,hi):
    raw=rng.standard_normal(N).astype(np.float32)
    sos=signal.butter(3,[lo,hi],fs=sr,btype="bandpass",output="sos")
    x=signal.sosfilt(sos,raw).astype(np.float32)
    rms=np.sqrt(np.mean(np.square(x,dtype=np.float64)))
    return x/max(rms,1e-8)
wind=amp(min(-42,voice_rms-27))
city=amp(min(-49,voice_rms-34))
a=noise(100,650)*(0.83+0.17*np.sin(t*.45))*wind
b=noise(100,650)*(0.82+0.18*np.sin(t*.49+.6))*wind
distant=noise(80,290)*city
L=a+distant; R=b+distant*.85
def chirp(channel,sec,up,down):
    i=int(sec*sr);n=int(.18*sr);tt=np.arange(n,dtype=np.float32)/sr
    e=np.sin(np.pi*np.arange(n,dtype=np.float32)/n)**1.8
    ph=2*np.pi*(up*tt+(down-up)*tt*tt/.36)
    channel[i:i+n]+=(np.sin(ph)*e*amp(min(-36,voice_rms-17))*.8).astype(np.float32)
for sec,lo,hi in [(5.7,1900,2180),(6.0,2120,1760),(48.5,1800,2010)]:
    chirp(L,sec,lo,hi);chirp(R,sec+.009,lo,hi)
def rustle(channel,sec,duration,level):
    i=int(sec*sr);n=int(duration*sr)
    z=rng.standard_normal(n).astype(np.float32)
    filt=signal.butter(2,[450,3500],fs=sr,btype="bandpass",output="sos")
    z=signal.sosfilt(filt,z).astype(np.float32)
    z/=max(np.sqrt(np.mean(np.square(z,dtype=np.float64))),1e-8)
    env=np.sin(np.pi*np.arange(n)/n)**2
    channel[i:i+n]+=(z*env*amp(level)).astype(np.float32)
foley_level=min(-41,voice_rms-22)
for sec,duration in [(12.8,.36),(14.45,.24),(16.4,.4)]:
    rustle(L,sec,duration,foley_level)
    rustle(R,sec+.01,duration,foley_level-2)
fade=np.minimum(1,t/1.4)*np.minimum(1,(N/sr-t)/1.8)
L*=fade;R*=fade
stereo=np.stack((L,R),axis=1)
peak=float(np.max(np.abs(stereo)))
assert peak<.05
pcm=np.asarray(np.clip(stereo,-1,1)*32767,dtype="<i2")
bed=AudioSegment(data=pcm.tobytes(),sample_width=2,frame_rate=sr,channels=2).set_frame_rate(44100)
bed=bed[:len(voice)]
if len(bed)<len(voice):bed+=AudioSegment.silent(duration=len(voice)-len(bed),frame_rate=44100).set_channels(2)
results={}
for name,gain in [("soft",-3.5),("natural",3)]:
    ambient=bed.apply_gain(gain)
    mixed=voice.overlay(ambient)
    output=OUT/("cassian-courtyard-"+name+"-ambience.mp3")
    mixed.export(output,format="mp3",bitrate="192k")
    results[name]={"filename":output.name,"duration_seconds":len(mixed)/1000,"bytes":output.stat().st_size,
                   "voice_rms_dbfs":round(voice_rms,1),"ambient_rms_dbfs":round(ambient.dBFS,1),
                   "peak_dbfs":round(mixed.max_dBFS,1)}
report={"status":"preview-only-not-published","source":"cassian-dialogue-quiet-edges-a-b-test.mp3",
        "soundscape":"childhood residential courtyard, faint outdoor air, distant town hum, two distant birds, water and cloth rustles",
        "no_rain":True,"no_music":True,"third_party_assets":False,"spoken_clips_unchanged":True,
        "speech_edits":0,"maximum_sfx_linear_peak":peak,"variants":results}
(OUT/"courtyard-ambience-report.json").write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2),flush=True)
