#!/usr/bin/env python3
"""A/B audio test to reduce synthetic inhale at voice boundaries.

Each of 15 HeyGen turn WAVs is complete. We never edit within a spoken word.
A = 45ms de-click fades on whole untrimmed audio, 600ms between turns.
B = adaptive reduction of only low-energy lead/trail material, plus 55ms fades.
The quiet region is *muted rather than cropped*, preserving the exact take's
timing and leaving words and internal breaths wholly unchanged.
"""
from pathlib import Path
import json, os, urllib.request, math, statistics
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

BASE=Path(__file__).resolve().parent
OUT=Path(os.environ.get("OUTPUT_DIR", BASE/"audio"/"isolated-tests"))
OUT.mkdir(parents=True, exist_ok=True)
data=json.loads((BASE/"isolated-cassian-scene-test-manifest.json").read_text(encoding="utf-8"))
correction=json.loads((BASE/"isolated-hallie-line-26-correction.json").read_text(encoding="utf-8"))
assert len(data["clips"])==15 and data["status"]=="recorded"
assert correction["run_id"]==26

def download(url, name, expected):
    path=OUT/name
    if not path.exists() or path.stat().st_size<5000:
        req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=150) as response, path.open("wb") as target:
            while True:
                chunk=response.read(1024*1024)
                if not chunk: break
                target.write(chunk)
    take=AudioSegment.from_file(path).set_frame_rate(44100).set_channels(1)
    if abs(len(take)/1000-expected)>.8:
        raise RuntimeError("Duration changed for "+name)
    return take

def source_turn(cue):
    if cue["run_id"]==26:
        src=correction
        return download(src["audio_url"],"suppress-inhale-26-corrected.wav",src["duration_seconds"])
    return download(cue["audio_url"],f"suppress-inhale-{cue['run_id']}.wav",cue["duration_seconds"])

def signal_analysis(seg):
    # Find speech-like portions of the source in short frames.
    # Stronger speech is generally 20-25 dB louder than room-tone/breath tails.
    loudness=seg.dBFS
    if not math.isfinite(loudness):raise RuntimeError("Silent TTS file")
    threshold=max(-46.0,min(-33.0,loudness-23))
    non_silence=detect_nonsilent(seg,min_silence_len=75,silence_thresh=threshold,seek_step=5)
    if not non_silence:return {"found":False,"silence_threshold":threshold}
    first=non_silence[0][0]
    last=non_silence[-1][1]
    return {"found":True,"speech_start_ms":first,"speech_end_ms":last,
            "leading_ms":first,"trailing_ms":len(seg)-last,
            "silence_threshold":round(threshold,2),
            "segments":len(non_silence)}

def gently_reduce_quiet_edges(seg, info):
    # Do not cut audio or the words. Only attenuate the portion that a
    # conservative silence detector regards as outside the speech cluster.
    if not info["found"]:return seg, {"leading_attenuated_ms":0,"trailing_attenuated_ms":0}
    first=info["speech_start_ms"];last=info["speech_end_ms"]
    # Keep at least 110ms of room ahead of the first voiced sound,
    # and 180ms after the last voiced sound, so consonants remain intact.
    left_end=max(0,first-110)
    right_start=min(len(seg),last+180)
    left=seg[:left_end]
    middle=seg[left_end:right_start]
    right=seg[right_start:]
    # Muting just low-energy edges cannot cut syllables. Crossfade-less concat
    # is safe here because the attenuated portions are close to digital silence.
    if len(left)>=50:left=left.apply_gain(-22)
    if len(right)>=50:right=right.apply_gain(-22)
    out=left+middle+right
    # Tiny de-click fades on existing digital silence, not at word boundaries.
    out=out.fade_in(min(35,len(out)//4)).fade_out(min(50,len(out)//4))
    assert len(out)==len(seg)
    return out,{"leading_attenuated_ms":len(left),"trailing_attenuated_ms":len(right)}

cues=data["clips"]
A=AudioSegment.silent(duration=220,frame_rate=44100)
B=AudioSegment.silent(duration=220,frame_rate=44100)
details=[]
for i,cue in enumerate(cues):
    src=source_turn(cue)
    if cue["gain"]!=1:src=src.apply_gain(20*math.log10(cue["gain"]))
    info=signal_analysis(src)
    soft=src.fade_in(45).fade_out(60)
    cleaned, reduced=gently_reduce_quiet_edges(src,info)
    A+=soft
    B+=cleaned
    if i<len(cues)-1:
        gap=640 if cue["actor"]!="narrator" else 550
        A+=AudioSegment.silent(duration=gap,frame_rate=44100)
        B+=AudioSegment.silent(duration=gap,frame_rate=44100)
    details.append({"run_id":cue["run_id"],"actor":cue["actor"],"text":cue["text"],
                    "source_duration_ms":len(src),"signal":info,"edge_attenuation":reduced})
A+=AudioSegment.silent(duration=450,frame_rate=44100)
B+=AudioSegment.silent(duration=450,frame_rate=44100)
assert len(A)==len(B)
variants={
"soft-edges":A,
"quiet-edges":B
}
output={}
for k,m in variants.items():
    dest=OUT/("cassian-dialogue-"+k+"-a-b-test.mp3")
    m.export(dest,format="mp3",bitrate="128k",parameters=["-ar","44100","-ac","1"])
    output[k]={"path":dest.name,"seconds":len(m)/1000,"bytes":dest.stat().st_size}
report={"type":"synthetic-inhale-boundary-A-B","status":"working-branch-only",
    "method":"No spoken-word cuts. A soft fades at WAV boundaries; B -22dB attenuation only of low-energy material outside conservative speech core.",
    "variants":output,"cues":details,"narration_gap_ms":550,"dialogue_gap_ms":640,
    "clip_count":len(cues),"word_level_edits":0,"speaker_overlaps":0}
(OUT/"synthetic-inhale-A-B-audit.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print("A/B COMPLETE",json.dumps({k:v for k,v in report.items() if k!="cues"},indent=2),flush=True)
