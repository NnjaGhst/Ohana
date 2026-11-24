"""Transcribe one WAV with faster-whisper. Cache keys include the adapter command."""
import argparse
import json

from faster_whisper import WhisperModel

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model", required=True, help="Prefer an immutable local model snapshot")
parser.add_argument("--language", default="en")
parser.add_argument("wav")
args = parser.parse_args()
model = WhisperModel(args.model, device="cpu", compute_type="int8")
segments, _ = model.transcribe(args.wav, language=args.language, word_timestamps=True,
                              condition_on_previous_text=False)
segments = list(segments)
words = [dict(text=word.word, start=word.start, end=word.end, probability=word.probability)
         for segment in segments for word in (segment.words or [])]
print(json.dumps({"text": "".join(segment.text for segment in segments), "words": words,
                  "confidence": min((word["probability"] for word in words), default=0.0)}))
