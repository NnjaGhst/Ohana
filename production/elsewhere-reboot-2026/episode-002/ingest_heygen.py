"""Download and verify a private HeyGen WAV batch without logging source URLs."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

EPISODE = 'elsewhere-reboot-2026-002'
HOST = 'resource2.heygen.ai'
SHA256 = re.compile(r'[0-9a-f]{64}')
MAX_BYTES = 200 * 1024 * 1024


def allowed(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.netloc == HOST
            and parsed.path.startswith('/text_to_speech/'))


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError('Unexpected download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def probe(path, reported):
    with path.open('rb') as source:
        header = source.read(12)
    if header[:4] not in (b'RIFF', b'RF64') or header[8:] != b'WAVE':
        raise ValueError('Download is not a WAV')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                             'stream=codec_name,channels,sample_rate:format=duration',
                             '-of', 'json', str(path)], capture_output=True, check=True)
    details = json.loads(result.stdout)
    stream, = details['streams']
    seconds = float(details['format']['duration'])
    if not math.isfinite(seconds) or not 0 < seconds <= 480:
        raise ValueError('Invalid WAV duration')
    if reported is not None and (not math.isfinite(reported) or abs(seconds - reported) > 0.1):
        raise ValueError('WAV duration differs from the source index')
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                    '-map', '0:a:0', '-f', 'null', '-'], capture_output=True, check=True)
    return dict(duration_seconds=seconds, sample_rate=int(stream['sample_rate']),
                channels=stream['channels'], codec=stream['codec_name'])


def ingest(take, directory, opener):
    path = directory / (take['take_id'] + '.wav')
    expected = take.get('sha256')
    if expected is not None and not SHA256.fullmatch(expected):
        raise ValueError('Invalid expected SHA-256')
    if not allowed(take['url']):
        raise ValueError('Unexpected HeyGen source URL')
    with tempfile.NamedTemporaryFile(dir=directory, prefix='.incoming-', delete=False) as output:
        temporary = Path(output.name)
        try:
            with opener.open(take['url'], timeout=45) as response:
                if response.status != 200:
                    raise ValueError('Expected a complete HTTP response')
                size = 0
                while chunk := response.read(256 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('Source exceeds the WAV size limit')
                    output.write(chunk)
                declared = response.headers.get('Content-Length')
                if declared is not None and size != int(declared):
                    raise ValueError('Incomplete download')
            output.flush()
            actual = digest(temporary)
            if expected is not None and actual != expected:
                raise ValueError('Source checksum mismatch')
            metadata = probe(temporary, take.get('duration_seconds'))
            try:
                path.hardlink_to(temporary)
            except FileExistsError:
                if path.is_symlink() or digest(path) != actual:
                    raise ValueError('Take ID already exists with different bytes') from None
            return dict(cue_id=take['cue_id'], take_id=take['take_id'], filename=path.name,
                        sha256=actual, source_hash_matched=expected is not None, bytes=size,
                        **metadata, status='download_verified')
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    index = json.loads(args.index.read_text(encoding='utf-8'))
    if index['episode_id'] != EPISODE or not index['takes']:
        parser.error('Expected a non-empty Episode 2 batch')
    identifiers = set()
    for take in index['takes']:
        cue, identifier = take['cue_id'], take['take_id']
        if (not re.fullmatch(r's\d{2}-c\d{3}', cue)
                or not re.fullmatch(re.escape(cue) + r'-t\d{2,}', identifier)
                or identifier in identifiers):
            parser.error('Invalid or duplicate cue/take identity')
        identifiers.add(identifier)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    opener = build_opener(SameHostRedirect())
    failed = False
    for take in index['takes']:
        try:
            print(json.dumps(ingest(take, args.output, opener)), flush=True)
        except Exception as error:
            # Avoid URL-bearing exceptions and FFmpeg diagnostics in shared logs.
            detail = str(error) if type(error) is ValueError and str(error) in {
                'Unexpected download redirect', 'Invalid expected SHA-256', 'Unexpected HeyGen source URL',
                'Source exceeds the WAV size limit', 'Incomplete download', 'Source checksum mismatch',
                'Expected a complete HTTP response',
                'Take ID already exists with different bytes', 'Download is not a WAV',
                'Invalid WAV duration', 'WAV duration differs from the source index',
            } else f'Unable to retrieve or decode source ({type(error).__name__})'
            print(json.dumps(dict(take_id=take['take_id'], status='blocked', reason=detail)), flush=True)
            failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
