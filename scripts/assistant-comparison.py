#!/usr/bin/env python3
"""Synthetic local-model/runtime probes; never deploy, deliver, or replay turns.

Raw receipts stay in the operator-owned output directory. The relay forwards
only bounded automation-moe inference to the existing verified-TLS LAN gateway.
It never forwards caller credentials or opens a model/admin endpoint elsewhere.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import stat
import statistics
import subprocess
import threading
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'benchmarks/assistant-comparison/cases.json'
NATIVE_PREFIX = ['env', 'PATH=/home/hermes-operator/.local/bin:/home/hermes-operator/.npm-global/bin:/usr/local/bin:/usr/bin:/bin',
                 'NEMOCLAW_GATEWAY_PORT=9123', 'nemoclaw', 'agent-openclaw', 'exec', '--', 'openclaw']


def save(path: Path, value: object) -> None:
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def private_output(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('Absolute private output directory required')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Output directory requires operator ownership and mode 0700')


def native_completed(value: dict, session: str) -> bool:
    meta = value.get('result', {}).get('meta', {})
    agent = meta.get('agentMeta', {})
    receipt = agent.get('terminalReceipt', {})
    return (value.get('status') == 'ok' and meta.get('aborted') is False
            and agent.get('sessionId') == session and receipt.get('sessionId') == session
            and agent.get('provider') == 'inference' and agent.get('model') == 'automation-moe'
            and receipt.get('effective', {}).get('provider') == 'inference'
            and receipt.get('effective', {}).get('model') == 'automation-moe'
            and receipt.get('rerouted') is False
            and meta.get('completion', {}).get('finishReason') == 'stop'
            and meta.get('completion', {}).get('stopReason') == 'stop'
            and not any(state.get('continuationPending') or state.get('yielded')
                        or state.get('pendingToolCalls') for state in [value, value.get('result', {}), meta]))


def bounded_payload(value: object) -> dict:
    if not isinstance(value, dict) or value.get('model') != 'automation-moe':
        raise ValueError('Only the local qualification alias is permitted')
    if value.get('tools') or value.get('n', 1) != 1:
        raise ValueError('Relay is restricted to single no-tool diagnostic requests')
    result = dict(value)
    limit = result.get('max_tokens', result.get('max_completion_tokens', 1024))
    if type(limit) is not int or limit <= 0:
        raise ValueError('Positive output-token budget required')
    result['max_tokens'] = min(limit, 1024)
    result.pop('max_completion_tokens', None)
    return result


def grade(case: dict, answer: str) -> dict:
    # Record strict-format and semantic checks separately: prose around correct
    # JSON is a format failure, not automatically a calculation failure.
    try:
        parsed = json.loads(answer)
        strict = True
    except ValueError:
        strict = False
        match = re.search(r'\{.*\}', answer, re.S)
        try:
            parsed = json.loads(match.group()) if match else None
        except ValueError:
            parsed = None
    checks = {}
    for key, expected in case['expected_json'].items():
        actual = parsed.get(key, object()) if isinstance(parsed, dict) else object()
        checks[key] = type(actual) is type(expected) and actual == expected
    for key, items in case.get('required_items', {}).items():
        actual = parsed.get(key) if isinstance(parsed, dict) else None
        checks[key] = isinstance(actual, list) and all(x in actual for x in items)
    return {'strict_json': strict, 'semantic_checks': checks,
            'semantic_pass': all(checks.values()), 'pass': strict and all(checks.values())}


def selected(case_id: str | None) -> tuple[dict, list[dict]]:
    fixtures = json.loads(FIXTURES.read_text())
    cases = [x for x in fixtures['cases'] if (x['id'] == case_id if case_id else not x.get('exclude_from_score'))]
    if not cases:
        raise ValueError('Unknown case')
    return fixtures, cases


def probe(args: argparse.Namespace) -> None:
    fixtures, cases = selected(args.case)
    private_output(args.output)
    for case in cases:
        target = args.output / (args.mode + '-' + case['id'] + '.json')
        if target.exists():
            raise ValueError('Receipt already exists; no replay attempted')
        session = str(uuid.uuid4())
        prompt = fixtures['instructions'] + '\n\n' + case['prompt']
        started = time.monotonic()
        if args.mode == 'native':
            native = [*NATIVE_PREFIX, 'agent', '--agent', case['role'], '--session-id', session,
                      '--message', prompt, '--timeout', '180', '--json']
            command = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                       '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=10',
                       '-o', 'ServerAliveCountMax=2', '-J', 'home-core', '-i',
                       str(Path.home() / '.ssh/id_ed25519_ai-services-01'),
                       'hermes-operator@10.77.20.2', shlex.join(native)]
            # Never retry a timeout: the remote turn may still be executing.
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=210)
            except subprocess.TimeoutExpired:
                save(target, {'case': case['id'], 'session_id': session, 'uncertain': True,
                              'error': 'external_deadline_no_replay', 'elapsed_seconds': time.monotonic()-started})
                raise SystemExit('Native outcome uncertain; stop and reconcile original session') from None
            (args.output / ('native-' + case['id'] + '.stdout')).write_text(result.stdout)
            (args.output / ('native-' + case['id'] + '.stderr')).write_text(result.stderr)
            value = json.loads(result.stdout)
            raw = value.get('result', {})
            meta = raw.get('meta', {})
            agent = meta.get('agentMeta', {})
            answer = '\n'.join(p.get('text', '') for p in raw.get('payloads', []))
            receipt = agent.get('terminalReceipt', {})
            identity = native_completed(value, session)
            record = {'case': case['id'], 'mode': args.mode, 'role': case['role'], 'session_id': session,
                      'exit': result.returncode, 'status': value.get('status'), 'identity_verified': identity,
                      'usage': agent.get('usage'), 'duration_ms': meta.get('durationMs'),
                      'tool_summary': meta.get('toolSummary'), 'completion': meta.get('completion'),
                      'continuation_pending': meta.get('continuationPending'),
                      'successful_tools': receipt.get('successfulToolNames', []),
                      'answer': answer, 'grade': grade(case, answer)}
            report = meta.get('systemPromptReport', {})
            record['prompt_report'] = {
                'system_chars': report.get('systemPrompt', {}).get('chars'),
                'project_context_chars': report.get('systemPrompt', {}).get('projectContextChars'),
                'skill_prompt_chars': report.get('skills', {}).get('promptChars'),
                'tool_schema_chars': report.get('tools', {}).get('schemaChars'),
                'tool_count': len(report.get('tools', {}).get('entries', [])),
                'tool_schemas': [{'name': x.get('name'), 'chars': x.get('schemaChars')}
                                 for x in report.get('tools', {}).get('entries', [])],
            }
            unexpected = set(record['successful_tools']) - {'read', 'agents_list', 'sessions_spawn', 'sessions_yield'}
            if unexpected:
                record['unexpected_tools'] = sorted(unexpected)
        else:
            body = {'model': 'automation-moe', 'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 1024, 'temperature': 0, 'stream': False,
                    'reasoning_effort': 'none'}
            request = urllib.request.Request('http://127.0.0.1:18796/v1/chat/completions',
                       data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=190) as response:
                value = json.load(response)
            save(args.output / ('direct-' + case['id'] + '.raw.json'), value)
            answer = value['choices'][0]['message'].get('content') or ''
            record = {'case': case['id'], 'mode': args.mode, 'usage': value.get('usage'),
                      'model': value.get('model'), 'answer': answer, 'grade': grade(case, answer)}
        record['elapsed_seconds'] = round(time.monotonic()-started, 3)
        record['fixture_sha256'] = hashlib.sha256(FIXTURES.read_bytes()).hexdigest()
        save(target, record)
        print(json.dumps({k:v for k,v in record.items() if k not in {'answer','completion'}}), flush=True)
        if record.get('unexpected_tools'):
            raise SystemExit('Unexpected native tools; inspect original receipt before more probes')
        if args.mode == 'native' and not record['identity_verified']:
            raise SystemExit('Native completion unverified; inspect original session without replay')


class Gateway(http.client.HTTPSConnection):
    def connect(self) -> None:
        self.sock = self._context.wrap_socket(socket.create_connection(('192.168.30.122', 443), self.timeout),
                                              server_hostname='ai.home.arpa')


def relay(args: argparse.Namespace) -> None:
    context = ssl.create_default_context(cafile=str(ROOT / 'artifacts/home-core-root.crt'))
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.send_error(404)
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2*1024*1024:
                self.send_error(413)
                return
            try:
                value = bounded_payload(json.loads(self.rfile.read(size)))
            except (ValueError, TypeError):
                self.send_error(400)
                return
            if not lock.acquire(blocking=False):
                self.send_error(429)
                return
            connection = Gateway('ai.home.arpa', timeout=180, context=context)
            try:
                connection.request('POST', self.path, json.dumps(value).encode(),
                                   {'Content-Type':'application/json', 'Host':'ai.home.arpa'})
                response = connection.getresponse()
                self.send_response(response.status)
                self.send_header('Content-Type', response.getheader('Content-Type','application/json'))
                self.end_headers()
                while chunk := response.read1(8192):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                self.close_connection = True
            finally:
                connection.close()
                lock.release()
    print('Loopback-only fixed local inference relay ready', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 18796), Handler).serve_forever()


def summarize(args: argparse.Namespace) -> None:
    private_output(args.output)
    fixtures, cases = selected(None)
    rows = []
    for case in cases:
        for mode in ('direct', 'rakazo-runtime', 'native'):
            directory = args.output / ('probes' if mode == 'native' and case['id'] == 'budget-en' else 'aligned')
            record = json.loads((directory / (mode + '-' + case['id'] + '.json')).read_text())
            assessment = grade(case, record['answer'])
            usage = record.get('usage') or {}
            if mode == 'native':
                if not record.get('identity_verified') or record.get('status') != 'ok':
                    raise ValueError('Native identity/completion not verified')
                if not {'input', 'output'} <= usage.keys():
                    raise ValueError('Native token measurements unavailable')
                input_tokens = usage.get('input', 0) + usage.get('cacheRead', 0) + usage.get('cacheWrite', 0)
                cache_tokens = usage.get('cacheRead', 0)
                reasoning = usage.get('reasoningTokens', 0)
                output_tokens = usage.get('output', 0)
            elif mode == 'direct':
                input_tokens = usage['prompt_tokens']
                cache_tokens = usage.get('prompt_tokens_details', {}).get('cached_tokens', 0)
                reasoning = usage.get('completion_tokens_details', {}).get('reasoning_tokens', 0)
                output_tokens = usage['completion_tokens']
            else:
                if not record.get('finished'):
                    raise ValueError('Rakazo runtime did not finish')
                if not usage or any(u.get('inputTokens') is None or u.get('outputTokens') is None for u in usage):
                    raise ValueError('Rakazo token measurements unavailable')
                input_tokens = sum((u.get('inputTokens') or 0) + (u.get('cacheReadTokens') or 0)
                                   + (u.get('cacheWriteTokens') or 0) for u in usage)
                cache_tokens = sum(u.get('cacheReadTokens') or 0 for u in usage)
                reasoning = sum(u.get('reasoningTokens') or 0 for u in usage)
                output_tokens = sum(u.get('outputTokens') or 0 for u in usage)
            rows.append({'case': case['id'], 'mode': mode,
                         'elapsed_seconds': round(record['elapsed_seconds'], 3),
                         'native_duration_ms': record.get('duration_ms'),
                         'input_tokens_including_cache': input_tokens, 'cache_read_tokens': cache_tokens,
                         'output_tokens_including_reasoning': output_tokens, 'reasoning_tokens': reasoning,
                         'semantic_pass': assessment['semantic_pass'], 'strict_json': assessment['strict_json'],
                         'pass': assessment['pass'], 'fixture_sha256': record.get('fixture_sha256'),
                         'successful_tools': record.get('successful_tools', [])})
    summaries = {}
    for mode in ('direct', 'rakazo-runtime', 'native'):
        group = [r for r in rows if r['mode'] == mode]
        summaries[mode] = {'samples': len(group), 'semantic_passes': sum(r['semantic_pass'] for r in group),
                           'strict_json_passes': sum(r['strict_json'] for r in group),
                           'median_elapsed_seconds': round(statistics.median(r['elapsed_seconds'] for r in group), 3),
                           'median_input_tokens_including_cache': statistics.median(r['input_tokens_including_cache'] for r in group)}
    report = {'schema_version': 1, 'scope': 'synthetic no-tool runtime diagnostic; not whole-product parity',
              'baseline': json.loads((args.output / 'baseline-sanitized.json').read_text()),
              'excluded_cases': [{k: c[k] for k in ('id', 'exclusion_reason')}
                                 for c in fixtures['cases'] if c.get('exclude_from_score')],
              'rows': rows, 'summary': summaries}
    save(args.output / 'summary.json', report)
    print(json.dumps(summaries, indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['relay','native','direct','summarize'])
    parser.add_argument('--case')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'relay':
        relay(args)
    elif args.output is None:
        parser.error('--output is required')
    elif args.mode == 'summarize':
        summarize(args)
    else:
        probe(args)


if __name__ == '__main__':
    main()
