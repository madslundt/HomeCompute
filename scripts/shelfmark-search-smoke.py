#!/usr/bin/env python3
"""Run on home-core as root; test authenticated searches without downloading books."""

import http.cookiejar
import json
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def main():
    values = {}
    with open('/run/secrets/books_importer/environment') as secret_file:
        for line in secret_file:
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.strip().split('=', 1)
                values[key] = shlex.split(value)[0]
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    base = 'http://127.0.0.1:8084/api'
    login = urllib.request.Request(
        base + '/auth/login',
        data=json.dumps({
            'username': values['SHELFMARK_USERNAME'],
            'password': values['SHELFMARK_PASSWORD'],
        }).encode(),
        headers={'Content-Type': 'application/json'},
    )
    opener.open(login, timeout=10).close()
    with opener.open(base + '/config', timeout=10) as response:
        config = json.load(response)
    mode = ('releases' if '--releases' in sys.argv else
            'direct' if '--direct' in sys.argv else config['search_mode'])
    print(f"Shelfmark {config['release_version']}; search={mode}; "
          f"release timeout={config['release_search_timeout']}s", flush=True)
    # Release fixtures must be books known to be indexed by the download source.
    queries = ('harry potter', 'the hobbit') if mode == 'releases' else (
        'harry potter', 'witch mcfadden'
    )
    for query in queries:
        params = {'query': query}
        if mode == 'releases':
            metadata_params = urllib.parse.urlencode({
                'query': query, 'limit': 1, 'content_type': 'ebook',
            })
            with opener.open(base + '/metadata/search?' + metadata_params,
                             timeout=15) as response:
                books = json.load(response).get('books', [])
            if not books:
                print(f'FAIL {query}: no catalogue fixture', flush=True)
                return 1
            book = books[0]
            path = '/releases'
            params = {
                'provider': book['provider'], 'book_id': book['provider_id'],
                'title': book['title'], 'source': 'direct_download',
                'content_type': 'ebook', 'languages': 'en,da',
            }
            result_key = 'releases'
            timeout = min(config['release_search_timeout'] + 15, 90)
        elif mode == 'direct':
            path = '/releases'
            params.update(source='direct_download', lang=['en', 'da'])
            result_key = 'releases'
            timeout = min(config['release_search_timeout'] + 15, 90)
        else:
            path = '/metadata/search'
            params.update(limit=10, content_type='ebook')
            result_key = 'books'
            timeout = 15
        started = time.monotonic()
        try:
            with opener.open(base + path + '?' + urllib.parse.urlencode(params, doseq=True),
                             timeout=timeout) as response:
                data = json.load(response)
            count = len(data.get(result_key, []))
            if not count:
                print(f'FAIL {query}: no results', flush=True)
                return 1
            print(f'PASS {query}: {count} results in {time.monotonic() - started:.2f}s',
                  flush=True)
        except (OSError, ValueError) as error:
            print(f'FAIL {query}: {type(error).__name__} after '
                  f'{time.monotonic() - started:.2f}s', flush=True)
            if isinstance(error, urllib.error.HTTPError):
                print(error.read().decode()[:1000], flush=True)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
