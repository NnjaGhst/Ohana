"""Single-submit HeyGen REST adapter for the audiobook queue."""
import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = 'https://api.heygen.com'
MODELS = {'heygen-voice-1', 'heygen-voice-1-turbo', 'heygen-voice-1-turbo-free'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call(path, key, body=None):
    req = Request(HOST + path, data=None if body is None else json.dumps(body).encode(),
                  headers={'X-Api-Key': key, 'Content-Type': 'application/json'})
    with build_opener(NoRedirect()).open(req, timeout=180 if body else 20) as response:
        return json.load(response)


def main():
    key = os.environ.get('HEYGEN_API_KEY')
    if not key:
        raise SystemExit('HEYGEN_API_KEY is required in the private runner environment')
    if sys.argv[1:] == ['probe']:
        call('/v3/users/me', key)
        print('HeyGen authenticated successfully')
        return
    payload = json.load(sys.stdin)
    model = os.environ.get('HEYGEN_AUDIOBOOK_MODEL')
    if model not in MODELS or payload['account'] != os.environ.get('HEYGEN_AUDIOBOOK_ACCOUNT'):
        raise SystemExit('The authorized HeyGen account and model must be configured explicitly')
    if payload['speed'] != 1 or payload['pitch'] != 1:
        raise SystemExit('Unexpected recording settings')
    try:
        result = call('/v3/models/audio/tts', key, dict(
            model=model, voice_id=payload['voice_id'], text=payload['text'], language='en',
            speed=1, pitch_shift=0, with_timestamps=True))
    except HTTPError as error:
        known = {401: 'authorization', 402: 'quota', 403: 'authorization', 429: 'rate_limit'}
        print(json.dumps({'status': known.get(error.code, 'provider_error')}))
        return
    info = result['data']
    print(json.dumps(dict(status='ok', url=info['audio_url'], duration_seconds=info['duration'])))


if __name__ == '__main__':
    main()
