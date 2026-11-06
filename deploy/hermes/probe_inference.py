"""Explicit authenticated local inference proof; emit no credentials or payloads."""
import json
import os
import re
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    key = os.environ['COMPATIBLE_API_KEY']
    if not re.fullmatch(r'openshell:resolve:env:[A-Za-z0-9_]+', key):
        raise RuntimeError('OpenShell inference credential projection required')
    body = {'model': 'automation-moe', 'temperature': 0, 'max_tokens': 32,
            'chat_template_kwargs': {'enable_thinking': False},
            'messages': [{'role': 'user', 'content': 'Reply with exactly HERMES_INFERENCE_OK'}]}
    request = urllib.request.Request('http://ai.home.arpa:18080/v1/chat/completions',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(request, timeout=45) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError('Local inference probe failed with HTTP ' + str(error.code)) from None
    except Exception:
        raise RuntimeError('Local inference probe transport failed') from None
    content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
    if content.strip() != 'HERMES_INFERENCE_OK':
        raise RuntimeError('Local inference probe did not return the synthetic marker')
    print('{"authenticated_inference":true,"model":"automation-moe"}')


if __name__ == '__main__':
    main()
