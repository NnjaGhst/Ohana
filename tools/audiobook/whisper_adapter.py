"""Transcribe one WAV with faster-whisper; install it separately for live ASR."""
import argparse
import json
import math


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='small.en')
    parser.add_argument('--language', default='en')
    parser.add_argument('audio')
    args = parser.parse_args()
    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device='cpu', compute_type='int8')
    segments, _ = model.transcribe(args.audio, language=args.language, condition_on_previous_text=False)
    segments = list(segments)
    print(json.dumps({
        'text': ' '.join(segment.text.strip() for segment in segments),
        'confidence': min((min(1, math.exp(segment.avg_logprob)) for segment in segments), default=0),
        'segments': [{'start': segment.start, 'end': segment.end, 'text': segment.text}
                     for segment in segments],
    }))


if __name__ == '__main__':
    main()
