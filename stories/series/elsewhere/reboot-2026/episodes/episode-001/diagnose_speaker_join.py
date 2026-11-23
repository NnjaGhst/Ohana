#!/usr/bin/env python3
import json,os,urllib.request
from pathlib import Path
from pydub import AudioSegment
BASE=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR",BASE/"join-tests"))
OUT.mkdir(exist_ok=True,parents=True)
read=lambda n:json.loads((BASE/n).read_text())
old=read("atomic-repairs-3m18s-5m00s.json")["recordings"]
alternative=read("alternative-courtyard-voice-clips.json")["clips"]
def old_url(s):return next(x["result"]["audio_url"] for x in old if x["id"]==s)
urls={
"H-old":old_url("ninja-youre-pulling-my-leg-off"),
"W-old":old_url("cassian-yes-thats-treatment"),
"H-new":next(x["result"]["audio_url"] for x in alternative if x["speaker"]=="Hallie"),
"W-new":next(x["result"]["audio_url"] for x in alternative if x["speaker"]=="Wade")
}
clips={}
for k,url in urls.items():
  file=OUT/(k+".wav")
  urllib.request.urlretrieve(url,file)
  clips[k]=AudioSegment.from_file(file).set_channels(1).set_frame_rate(44100)
sil=lambda ms:AudioSegment.silent(duration=ms,frame_rate=44100)
cases=[
("old-old-long","H-old","W-old",900),
("new-old-long","H-new","W-old",900),
("old-new-long","H-old","W-new",900),
("new-new-long","H-new","W-new",900),
("old-old-short","H-old","W-old",340),
("new-new-short","H-new","W-new",340)
]
for label,h,w,gap_ms in cases:
  # Silence around the test eliminates the effect of larger episode context.
  seg=sil(600)+clips[h]+sil(gap_ms)+clips[w]+sil(700)
  seg.export(OUT/(label+".mp3"),format="mp3",bitrate="128k")
  print(label,"duration",len(seg)/1000,flush=True)
