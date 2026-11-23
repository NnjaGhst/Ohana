#!/usr/bin/env python3
"""Assemble Elsewhere reboot Episode 1 from approved speech sources.
No file is published by this script. Writes MP3, timing report, and QC report.
"""
import json, os, re, sys, urllib.request, subprocess
from pathlib import Path
from difflib import SequenceMatcher

from pydub import AudioSegment

BASE = Path(__file__).resolve().parent
OUT = Path(os.getenv("OUTPUT_DIR", str(BASE / "build")))
OUT.mkdir(exist_ok=True, parents=True)
read=lambda name:json.loads((BASE / name).read_text())
W, H, T, C = (read(x) for x in ("wade-recordings.json", "hallie-dialogue-recordings.json", "hallie-thoughts-recordings.json", "production-cues.json"))
A=read("wade-apartment-scene.json")

def source_file(url, name):
    target=OUT / name
    if target.exists() and target.stat().st_size>100000:
        return target
    print("Downloading",name, flush=True)
    req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as source, open(target,'wb') as f:
        while True:
            chunk=source.read(1024*1024)
            if not chunk: break
            f.write(chunk)
    if target.stat().st_size<100000:raise RuntimeError(f"Suspiciously short audio file: {name}")
    return target

audio={}
for clip in W["clips"]:
    p=source_file(clip["url"],f'wade_{clip["part"]:02}.wav')
    audio[("W",clip["part"])]=AudioSegment.from_file(p).set_channels(1).set_frame_rate(44100)
hpart=H["parts"][0]
audio[("H",1)]=AudioSegment.from_file(source_file(hpart["url"],"hallie_dialogue.wav")).set_channels(1).set_frame_rate(44100)
audio[("T",1)]=AudioSegment.from_file(source_file(T["url"],"hallie_thoughts.wav")).set_channels(1).set_frame_rate(44100)
audio[("A",1)]=AudioSegment.from_file(source_file(A["url"],"wade_apartment.wav")).set_channels(1).set_frame_rate(44100)

def align_first(wav,text):
    import stable_whisper
    model=stable_whisper.load_model("base.en",device="cpu")
    result=model.align(str(wav),text,language="en")
    words=[]
    for seg in result.segments:
        for item in seg.words:
            if isinstance(item,dict):
                token=str(item.get("word",item.get("text",""))).strip()
                begin=item.get("start",item.get("_start"))
                finish=item.get("end",item.get("_end"))
            else:
                token=str(getattr(item,"word",getattr(item,"text",""))).strip()
                begin=getattr(item,"start",getattr(item,"_start",None))
                finish=getattr(item,"end",getattr(item,"_end",None))
            norm=re.sub("[^a-z0-9]","",token.lower())
            if norm and begin is not None and finish is not None:
                words.append((norm,float(begin),float(finish)))
    src=[(m.group(),m.start(),m.end()) for m in re.finditer(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?",text)]
    a=[re.sub("[^a-z0-9]","",item[0].lower()) for item in src]
    b=[item[0] for item in words]
    matcher=SequenceMatcher(a=a,b=b,autojunk=False)
    mapping=[]
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            i,j=block.a+k,block.b+k
            mapping.append((src[i][1],src[i][2],words[j][1],words[j][2]))
    matched=len(mapping)/max(1,len(src))
    print("First clip alignment match fraction",round(matched,3),flush=True)
    if matched<0.86: raise RuntimeError("First clip forced alignment confidence too low; cannot publish.")
    return mapping,matched

# Read exact source text chunks used by synthesis.
wtext=W["scriptTextChunks"]
assert len(W["clips"])==len(wtext)==5
align,match=align_first(OUT/"wade_01.wav",wtext[0])
first_timing=align

def text_bounds(text,start,end,marks=None):
    if marks is not None:
        if not (0<=start<end<=len(marks)):raise RuntimeError("Text offset outside TTS timing array")
        return float(marks[start][0]),float(marks[end-1][1])
    hits=[(s,e) for a,b,s,e in first_timing if b>start and a<end]
    if not hits: raise RuntimeError(f"No aligned words for first Wade cue at {start}:{end}")
    return min(x[0] for x in hits),max(x[1] for x in hits)

def apply_gain(seg,gain):
    import math
    return seg.apply_gain(20*math.log10(gain))

# Speech timings describe words, not the fade and breaths around each word.
# Preserve the nearby recorded air without accidentally capturing the next line.
def extract_with_handles(seg, start, end, previous_end=None, next_start=None):
    left = min(65, max(0, round((start - previous_end)*500))) if previous_end is not None else 65
    right = min(105, max(0, round((next_start - end)*500))) if next_start is not None else 105
    begin=max(0,round(start*1000)-left)
    finish=min(len(seg),round(end*1000)+right)
    if finish<=begin:raise RuntimeError("Empty source audio extraction")
    return seg[begin:finish].fade_in(min(22,(finish-begin)//4)).fade_out(min(30,(finish-begin)//4))

def contextual_speech(seg, kind):
    # Quieter thought narration and spoken lines retain the exact same pitch.
    # Gap after Ninja speaks prevents Wade entering on top of the final syllable.
    before = 115 if kind=="thought" else 85
    after = 285 if kind=="thought" else 225
    return (AudioSegment.silent(duration=before,frame_rate=44100)
            + seg
            + AudioSegment.silent(duration=after,frame_rate=44100))

# Locate exact dialogue snippets in Hallie's continuous dialogue recording.
hcursor=0
hspans={}
for qid, line in zip(hpart["quoteIds"],hpart["quotedLines"]):
    index=hpart["rawText"].find(line,hcursor)
    if index<0:raise RuntimeError(f"Hallie quote not found: {qid}")
    hspans[qid]=text_bounds(hpart["rawText"],index,index+len(line),hpart["chars_timing"])
    hcursor=index+len(line)
assert len(hspans)==67

# Locate each inner thought block in the separate Hallie recording.
tcursor=0
tspans={}
for b in T["blocks"]:
    line=b["text"]
    index=T["rawText"].find(line,tcursor)
    if index<0:raise RuntimeError("Thought not found in Hallie audio: "+str(b["id"]))
    tspans[b["id"]]=text_bounds(T["rawText"],index,index+len(line),T["chars_timing"])
    tcursor=index+len(line)

replacements={i:[] for i in range(1,6)}
ordered_quotes = list(hpart["quoteIds"])
ordered_thoughts = [b["id"] for b in T["blocks"]]

def adjacent_times(mapping, ordered, id):
    i=ordered.index(id)
    prev_end=mapping[ordered[i-1]][1] if i else None
    next_start=mapping[ordered[i+1]][0] if i+1<len(ordered) else None
    return prev_end,next_start

for q in C["quotes"]:
    if q["speaker"]!="Ninja":continue
    part=q["part"]
    marks=W["clips"][part-1].get("chars_timing")
    st,en=text_bounds(wtext[part-1],q["start"],q["end"],marks)
    hs,he=hspans[q["id"]]
    previous_end,next_start=adjacent_times(hspans,ordered_quotes,q["id"])
    spoken=extract_with_handles(audio[("H",1)],hs,he,previous_end,next_start)
    replacements[part].append({"type":"dialogue","id":q["id"],"start":st,"end":en,
        "new":contextual_speech(apply_gain(spoken,0.90),"dialogue")})
for thought in C["thoughts"]:
    part=thought["part"]
    marks=W["clips"][part-1].get("chars_timing")
    st,en=text_bounds(wtext[part-1],thought["start"],thought["end"],marks)
    hs,he=tspans[thought["id"]]
    previous_end,next_start=adjacent_times(tspans,ordered_thoughts,thought["id"])
    spoken=extract_with_handles(audio[("T",1)],hs,he,previous_end,next_start)
    replacements[part].append({"type":"thought","id":thought["id"],"start":st,"end":en,
        "new":contextual_speech(apply_gain(spoken,0.80),"thought")})

# Remove only unneeded name/speech attributions immediately after Ninja's
# quoted speech. Never strip story-bearing action or other character speakers.
# Wade will still narrate surrounding action and all of Cassian's dialogue.
redundant_tags = [
    "Ninja muttered at it.", "Ninja said.", "she said.",
    "she demanded.", "she asked.", "Ninja asked.", "she managed."
]
removed_tags=[]
for part,source in enumerate(wtext,1):
    marks=W["clips"][part-1].get("chars_timing")
    for phrase in redundant_tags:
        offset=0
        while True:
            start=source.find(phrase,offset)
            if start<0:break
            offset=start+len(phrase)
            # Quote marks just before the tag distinguish speech attribution
            # from important narration containing 'she asked' / 'she said'.
            if '"' not in source[max(0,start-7):start]:continue
            end=start+len(phrase)
            st,en=text_bounds(source,start,end,marks)
            replacements[part].append({"type":"attribution","id":f"tag-{part}-{start}","start":st,
                "end":en,"new":AudioSegment.silent(duration=40,frame_rate=44100)})
            removed_tags.append({"part":part,"text":phrase,"start":round(st,3),"end":round(en,3)})
if len(removed_tags)<6:
    raise RuntimeError("Could not locate expected redundant speaker tags; review script revision")
print("Redundant attribution tags to remove:",removed_tags,flush=True)

# Insert the newly recorded extra scene immediately after the final grief vignette.
anchor=A["insertAfter"]
anchor_start=wtext[3].index(anchor)
anchor_end=anchor_start+len(anchor)
insert_at=text_bounds(wtext[3],anchor_start,anchor_end,W["clips"][3]["chars_timing"])[1]
interlude=AudioSegment.silent(duration=300,frame_rate=44100)+audio[("A",1)]+AudioSegment.silent(duration=450,frame_rate=44100)
replacements[4].append({"type":"insertion","id":"apartment-scene","start":insert_at,"end":insert_at,"new":interlude})

expected=67+4+1+len(removed_tags)
if sum(len(x) for x in replacements.values())!=expected:raise RuntimeError("Missing replacement cues")

finished=AudioSegment.silent(duration=0,frame_rate=44100)
report=[]
for part in range(1,6):
    seg=audio[("W",part)]
    spans=sorted(replacements[part],key=lambda x:x["start"])
    cursor=0
    piece=AudioSegment.silent(duration=0,frame_rate=44100)
    for cue in spans:
        # Small guard avoids preceding consonants and following words being removed.
        if cue["type"]=="insertion":
            st=round(cue["start"]*1000)
            en=st
        else:
            st=round(cue["start"]*1000)-12
            en=min(len(seg),round(cue["end"]*1000)+25)
            # A previous recorded sentence can have tiny timestamp spillover.
            # Clamp at the last cut rather than overlap or clip an earlier edit.
            st=max(cursor,st)
            en=max(st,en)
        if en<st:raise RuntimeError(f"Invalid cue time: {part} {cue['id']}")
        if st < cursor:raise RuntimeError(f"Overlapping cues: {part} {cue['id']}")
        preserved=seg[cursor:st]
        if cue["type"]!="insertion" and preserved:
            preserved=preserved.fade_out(min(12,len(preserved)//4))
        piece += preserved
        new=cue["new"]
        if cue["type"]=="insertion":
            new=new.fade_in(20).fade_out(20)
        piece += new
        report.append({"part":part,"id":cue["id"],"type":cue["type"],"wadeStart":st/1000,
            "wadeEnd":en/1000,"insertedSeconds":len(new)/1000})
        cursor=en
    piece += seg[cursor:]
    finished += piece

# No artificial effects, pitch changes, or speed changes.
output=OUT/"Elsewhere_Episode_01_A_Place_That_Doesnt_Remember.mp3"
finished.export(output,format="mp3",bitrate="96k",parameters=["-ar","44100","-ac","1"])
reportpath=OUT/"edit-report.json"
reportpath.write_text(json.dumps({"source_duration":sum(float(x["duration"]) for x in W["clips"]),
    "final_seconds":len(finished)/1000,"file_size":output.stat().st_size,
    "first_clip_word_match":match,
    "source_chunks":len(W["clips"]),"extra_narrative_scene_seconds":A["duration"],"hallie_lines":67,"hallie_thoughts":4,
    "mix_version":"2-clean-pauses-and-attributions", "removed_attribution_tags":removed_tags,
    "hallie_dialogue_absolute_gain":0.90,"hallie_thoughts_absolute_gain":0.80,
    "transition_pauses_ms":{"spoken_before":85,"spoken_after":225,"thought_before":115,"thought_after":285},
    "cues":report},indent=2))
if output.stat().st_size>25*1024*1024:raise RuntimeError("MP3 exceeds Ohana upload limit")
if not (25*60<len(finished)/1000<65*60):raise RuntimeError("Unexpected final runtime")
print("MASTER READY:",output,"duration",len(finished)/1000,"size",output.stat().st_size,flush=True)
