"""Download into the private work directory and round-trip a private archive copy."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pipeline
from recording_queue import SHA, SAFE_ID
from recording_queue import digest, require, save


def retain(source, target, checksum):
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.incoming-', delete=False) as output:
            path = Path(output.name)
            try:
                with source.open('rb') as stream:
                    shutil.copyfileobj(stream, output)
                output.flush()
                os.fsync(output.fileno())
                require(digest(path) == checksum, 'Archive copy changed')
                try:
                    target.hardlink_to(path)
                except FileExistsError:
                    pass
            finally:
                path.unlink(missing_ok=True)
    require(not target.is_symlink() and digest(target) == checksum,
            'Stored audio failed checksum readback')


def main():
    os.umask(0o077)
    body = json.load(sys.stdin)
    request, provider = body['request'], body['response']
    archive = Path(os.environ['HEYGEN_PRIVATE_ARCHIVE']).resolve(strict=True)
    work = Path(os.environ['HEYGEN_PRIVATE_WORK']).resolve(strict=True)
    require(archive != work and archive.is_dir() and work.is_dir(),
            'Use separate private work and durable archive directories')
    (work / 'takes').mkdir(exist_ok=True, mode=0o700)
    require(SHA.fullmatch(request['request_id']) and SAFE_ID.fullmatch(request['take_id']),
            'Unsafe private take identity')
    saved = work / (request['request_id'] + '.archive.json')
    if saved.is_file():
        receipt = json.loads(saved.read_text())
    else:
        with tempfile.TemporaryDirectory(dir=work) as tmp:
            incoming = Path(tmp)
            index = incoming / 'index.json'
            take = dict(cue_id=request['cue_id'], take_id=request['take_id'],
                        url=provider['url'], duration_seconds=provider['duration_seconds'])
            save(index, dict(episode_id='elsewhere-reboot-2026-002', takes=[take]))
            result = subprocess.run([sys.executable, os.environ['HEYGEN_INGEST_HELPER'],
                                     str(index), str(incoming)], capture_output=True, check=True, timeout=120)
            receipt = json.loads(result.stdout)
            key = receipt['sha256'] + '.wav'
            retain(incoming / receipt['filename'], archive / key, receipt['sha256'])
            save(saved, receipt)
    key = receipt['sha256'] + '.wav'
    require(SHA.fullmatch(receipt['sha256']) and receipt['filename'] == request['take_id'] + '.wav',
            'Invalid retained receipt')
    local = work / 'takes' / receipt['filename']
    retain(archive / key, local, receipt['sha256'])
    # The queue classifies lexical discrepancies. The full pipeline runs
    # contextual/audio QA and the existing review gate before a release.
    config = json.loads((work / 'asr.json').read_text())
    asr_cache = work / 'asr-cache'
    asr_cache.mkdir(exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        analysis = Path(tmp) / 'take.wav'
        pcm = pipeline.decode(local)
        flags = pipeline.signal_flags(pcm, take=True)
        # A diagnostic attenuation does not alter the retained original.
        if max(abs(pcm[:, 0])) >= 1:
            pcm = pcm * .5
        pipeline.wav(analysis, pcm)
        asr = pipeline.transcribe(analysis, config, asr_cache)
    print(json.dumps(dict(sha256=receipt['sha256'], bytes=receipt['bytes'],
                          duration_seconds=receipt['duration_seconds'], archive_key=key,
                          roundtrip_verified=True, verified_wav=str(local), transcript=asr['text'], signal_flags=flags)))


if __name__ == '__main__':
    main()
