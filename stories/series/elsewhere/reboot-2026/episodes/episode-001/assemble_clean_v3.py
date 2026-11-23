#!/usr/bin/env python3
"""Elsewhere Ep1 v3: safe paragraph-boundary audio assembly.

Critical change: never splice in the middle of a spoken sentence or short
speaker attribution. Ninja's *whole original Wade paragraph* is replaced by
Hallie's complete dialogue / thought reading at natural silence boundaries.
All other Wade paragraphs and the already recorded apartment scene are kept.
This script produces a preview, not a direct Ohana publication.
"""
import json, os, re, urllib.request, math
from pathlib import Path
from difflib import SequenceMatcher
from pydub import AudioSegment

BASE=Path(__file__).resolve().parent
OUT=Path(os.getenv("OUTPUT_DIR",str(BASE/"build-v3")))
OUT.mkdir(parents=True,exist_ok=True)
read=lambda n:json.loads((BASE/n).read_text())
W=read("wade-recordings.json")
H=read("hallie-dialogue-recordings.json")
T=read("hallie-thoughts-recordings.json")
C=read("production-cues.json")
A=read("wade-apartment-scene.json")

def download(url,name):
    target=OUT/name
    if target.exists() and target.stat().st_size>100000:return target
    with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"}),timeout=180) as response, open(target,"wb") as f:
        while data:=response.read(1024*1024):f.write(data)
    if target.stat().st_size<100000:raise RuntimeError("Invalid audio: "+str(target))
    return target

clips=[]
for clip in W["clips"]:
    clips.append(AudioSegment.from_file(download(clip["url"],f"wade-{clip['part']:02}.wav")).set_channels(1).set_frame_rate(44100))
hpart=H["parts"][0]
hallie=AudioSegment.from_file(download(hpart["url"],"hallie-dialogue.wav")).set_channels(1).set_frame_rate(44100)
thought_audio=AudioSegment.from_file(download(T["url"],"hallie-thoughts.wav")).set_channels(1).set_frame_rate(44100)
addendum=AudioSegment.from_file(download(A["url"],"wade-apartment.wav")).set_channels(1).set_frame_rate(44100)
texts=W["scriptTextChunks"]
assert len(texts)==len(clips)==5

def align_first():
    import stable_whisper
    model=stable_whisper.load_model("base.en",device="cpu")
    result=model.align(str(OUT/"wade-01.wav"),texts[0],language="en")
    words=[]
    for segment in result.segments:
        for item in segment.words:
            token=str(getattr(item,"word","")).strip()
            start=getattr(item,"start",None);end=getattr(item,"end",None)
            if token and start is not None and end is not None:
                norm=re.sub("[^a-z0-9]","",token.lower())
                if norm:words.append((norm,float(start),float(end)))
    originals=[(m.group(),m.start(),m.end()) for m in re.finditer(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?",texts[0])]
    a=[re.sub("[^a-z0-9]","",w[0].lower()) for w in originals]
    b=[w[0] for w in words]
    matched=[]
    for block in SequenceMatcher(a=a,b=b,autojunk=False).get_matching_blocks():
        for k in range(block.size):
            i=block.a+k;j=block.b+k
            matched.append((originals[i][1],originals[i][2],words[j][1],words[j][2]))
    fraction=len(matched)/max(1,len(originals))
    if fraction<.92:raise RuntimeError(f"Forced alignment too weak: {fraction}")
    print("Wade first chunk word matching",fraction,flush=True)
    return matched,fraction
first_marks,match=align_first()

def text_times(part,start,end):
    if not 0<=start<end<=len(texts[part-1]):raise RuntimeError(f"Bad source offset {part}:{start}:{end}")
    if part==1:
        hits=[(ts,te) for a,b,ts,te in first_marks if b>start and a<end]
        if not hits:raise RuntimeError(f"No speech timings for part {part}:{start}-{end}")
        return min(x[0] for x in hits),max(x[1] for x in hits)
    marks=W["clips"][part-1]["chars_timing"]
    if not marks or end>len(marks):raise RuntimeError(f"Missing character timings {part}")
    return float(marks[start][0]),float(marks[end-1][1])

def paragraphs(text):
    position=0;out=[]
    for p in re.split(r"\n\n+",text):
        if not p:continue
        start=text.find(p,position);end=start+len(p)
        if start<0:raise RuntimeError("Paragraph indexing failed")
        out.append({"text":p,"start":start,"end":end})
        position=end
    return out

def gain(seg,fraction):
    return seg.apply_gain(20*math.log10(fraction))

def locate_cues(full_text,lines,ids):
    times={};cursor=0
    for cue_id,line in zip(ids,lines):
        ix=full_text.find(line,cursor)
        if ix<0:raise RuntimeError(f"Hallie cue missing {cue_id} {line[:60]}")
        marks=hpart["chars_timing"]
        times[cue_id]=(float(marks[ix][0]),float(marks[ix+len(line)-1][1]))
        cursor=ix+len(line)
    return times

hq=locate_cues(hpart["rawText"],hpart["quotedLines"],hpart["quoteIds"])
ht={}
cursor=0
for t in T["blocks"]:
    ix=T["rawText"].find(t["text"],cursor)
    if ix<0:raise RuntimeError(f"Thought recording mismatch: {t['id']}")
    marks=T["chars_timing"]
    ht[t["id"]]=(float(marks[ix][0]),float(marks[ix+len(t["text"])-1][1]))
    cursor=ix+len(t["text"])

def extracted(seg,times,all_keys,key):
    t0,t1=times[key]
    ix=all_keys.index(key)
    prior=times[all_keys[ix-1]][1] if ix else None
    following=times[all_keys[ix+1]][0] if ix+1<len(all_keys) else None
    # Most of the available silent gap is kept, but not the next spoken line.
    left=min(.15,(t0-prior)*.45) if prior is not None else .07
    right=min(.17,(following-t1)*.45) if following is not None else .12
    left=max(.02,left);right=max(.03,right)
    a=max(0,round((t0-left)*1000));b=min(len(seg),round((t1+right)*1000))
    if b<=a:raise RuntimeError(f"Empty Hallie cut {key}")
    z=seg[a:b]
    return z.fade_in(min(15,len(z)//4)).fade_out(min(25,len(z)//4))

quote_ids=list(hpart["quoteIds"])
thought_ids=[x["id"] for x in T["blocks"]]

# A fixed set of full paragraphs (not individual words) is replaced.
replacement_sets={}
for part,text in enumerate(texts,1):
    pars=paragraphs(text)
    for p in pars:
        p["spoken_start"],p["spoken_end"]=text_times(part,p["start"],p["end"])
        p["quotes"]=[x for x in C["quotes"] if x["part"]==part and x["speaker"]=="Ninja" and p["start"]<=x["start"] and x["end"]<=p["end"]]
        p["thoughts"]=[x for x in C["thoughts"] if x["part"]==part and p["start"]<=x["start"] and x["end"]<=p["end"]]
    replacement_sets[part]=pars
assert sum(len(p["quotes"]) for ps in replacement_sets.values() for p in ps)==67
assert sum(len(p["thoughts"]) for ps in replacement_sets.values() for p in ps)==4

def min_energy_split(audio,lo,hi,prefer):
    """Find a near-silent point between neighboring paragraphs, safely."""
    duration=len(audio)
    lo=max(0,round(lo*1000));hi=min(duration,round(hi*1000))
    if hi<=lo:return min(duration,max(0,round(prefer*1000)))
    pref=min(hi,max(lo,round(prefer*1000)))
    best=(float("inf"),pref)
    # Search small windows for the quietest place (not a word onset).
    for point in range(lo,hi+1,10):
        segment=audio[max(0,point-12):min(duration,point+12)]
        db=segment.dBFS if segment and segment.rms>0 else -90.0
        score=db+abs(point-pref)/80.0
        if score<best[0]:best=(score,point)
    return best[1]

def paragraph_cuts(audio,pars):
    result=[0]
    for before,after in zip(pars,pars[1:]):
        prev_end=before["spoken_end"]
        next_start=after["spoken_start"]
        midpoint=(prev_end+next_start)/2
        if next_start>prev_end+.04:
            a=prev_end+.005;b=next_start-.005
            cut=min_energy_split(audio,a,b,midpoint)
        else:
            # Close or slightly overlapping timestamps: prefer the quietest
            # acoustic window instead of deleting syllables from either line.
            cut=min_energy_split(audio,midpoint-.075,midpoint+.075,midpoint)
        result.append(max(result[-1],cut))
    result.append(len(audio))
    if any(b<a for a,b in zip(result,result[1:])):raise RuntimeError("Out of order paragraph cuts")
    return result

# Original addendum was inserted after the end of the grief vignette.
# Preserve the exact anchor with a whole paragraph transition.
anchor=A["insertAfter"]
report=[]
final=AudioSegment.empty()
removed_dialogue_tags=[]
transitions=0
splices=0
for part,orig in enumerate(clips,1):
    pars=replacement_sets[part]
    cuts=paragraph_cuts(orig,pars)
    piece=AudioSegment.empty()
    for ix,p in enumerate(pars):
        q=p["quotes"];t=p["thoughts"]
        if q and t:raise RuntimeError("Mixed thought and dialogue in one paragraph")
        if not(q or t):
            segment=orig[cuts[ix]:cuts[ix+1]]
            piece+=segment
        else:
            # The entire original Wade paragraph is removed, including all
            # spoken "Ninja said", "she said", or "she asked" tags.
            if q:
                voice=AudioSegment.empty()
                for k,item in enumerate(q):
                    if k:voice+=AudioSegment.silent(duration=130,frame_rate=44100)
                    voice+=extracted(hallie,hq,quote_ids,item["id"])
                voice=gain(voice,0.90)
                typ="dialogue"
                clean=" ".join(item["text"] for item in q)
                if len(q)>1:clean=clean.replace(", ",". ",1)
            else:
                x=t[0]
                voice=gain(extracted(thought_audio,ht,thought_ids,x["id"]),0.80)
                typ="thought"
                clean=x["text"]
            piece+=AudioSegment.silent(duration=125 if typ=="dialogue" else 175,frame_rate=44100)
            piece+=voice
            piece+=AudioSegment.silent(duration=290 if typ=="dialogue" else 360,frame_rate=44100)
            splices+=1
            transitions+=2
            removed_dialogue_tags+=re.findall(r"\b(?:Ninja|she)\s+(?:said|asked|managed|demanded|muttered)\b",p["text"],flags=re.I)
            report.append({"part":part,"paragraph":ix+1,"kind":typ,
                "wade_source_start":round(cuts[ix]/1000,3),
                "wade_source_end":round(cuts[ix+1]/1000,3),
                "removed_wade_text":p["text"],
                "replacement_text":clean,"inserted_audio_seconds":round(len(voice)/1000,3)})
        if part==4 and anchor in p["text"]:
            piece+=AudioSegment.silent(duration=350,frame_rate=44100)+addendum+AudioSegment.silent(duration=550,frame_rate=44100)
            report.append({"part":4,"kind":"addendum","inserted_audio_seconds":len(addendum)/1000})
    final+=piece
    # Natural inter-segment pause rather than artificial crossfade.
    if part<5:final+=AudioSegment.silent(duration=300,frame_rate=44100)

if splices!=68:raise RuntimeError(f"Unexpected number of replaced paragraphs: {splices}")
if len(removed_dialogue_tags)<7:raise RuntimeError("Too few spoken Ninja tags removed")
if len(final)<24*60*1000 or len(final)>60*60*1000:raise RuntimeError("Unexpected master length")

name="Elsewhere_Episode_01_Clean_V3.mp3"
mp3=OUT/name
final.export(mp3,format="mp3",bitrate="96k",parameters=["-ar","44100","-ac","1"])
stats={"mix_version":"3-whole-paragraph-replacements",
"duration_seconds":len(final)/1000,"file_size":mp3.stat().st_size,
"first_clip_alignment_fraction":match,"spliced_paragraphs":splices,
"ninja_dialogue_lines":67,"thought_blocks":4,
"removed_dialogue_tags":removed_dialogue_tags,
"audio_editing_rule":"No midword / mid-tag edits; all Ninja content replaces entire Wade paragraph at acoustic paragraph boundaries",
"clip_overlap_count":0,
"replacements":report}
(OUT/"edit-report-v3.json").write_text(json.dumps(stats,indent=2))
print("V3 MIX COMPLETE",json.dumps({k:v for k,v in stats.items() if k not in ["replacements"]}),flush=True)
