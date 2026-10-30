"""Pure pinned OpenClaw performance candidates; no live writes or restarts."""
from __future__ import annotations

import copy
import hashlib

BROWSER_FILE = 'browser-tool.schema-CSTr80M_.js'
BROWSER_SHA256 = '96a7f6b569f0b928e7e6410bb8af140386a946f5f46ca4771633e9fb8b103918'


def reasoning_candidate(before: dict) -> dict:
    """Declare verified endpoint support; map off explicitly without forcing it on other turns."""
    after = copy.deepcopy(before)
    provider = after['models']['providers']['inference']
    if provider['baseUrl'] != 'https://inference.local/v1' or provider['api'] != 'openai-completions':
        raise ValueError('Unexpected inference route')
    models = [model for model in provider['models'] if model['id'] == 'automation-moe']
    if len(models) != 1:
        raise ValueError('One qualified model definition required')
    model = models[0]
    model['reasoning'] = True
    model.setdefault('thinkingLevelMap', {})['off'] = 'none'
    compat = model.setdefault('compat', {})
    compat['supportsReasoningEffort'] = True
    # Generic OpenAI-compatible models otherwise exclude "none" and the
    # provider silently omits the off control despite the capability flag.
    compat['supportedReasoningEfforts'] = ['none', 'low', 'medium', 'high', 'xhigh']
    return after


def browser_candidate(raw: bytes, helper_url: str) -> bytes:
    """Build an import-time overlay; the running gateway file is never edited by this function."""
    if hashlib.sha256(raw).hexdigest() != BROWSER_SHA256:
        raise ValueError('Pinned browser runtime changed; preserve drift and requalify')
    if not helper_url.startswith('file:///') or any(c in helper_url for c in ['"', "'", '\n', '\r', '\\']):
        raise ValueError('Exact operator-owned helper URL required')
    text = raw.decode()
    start = 'function describeBrowserTool(opts) {\n'
    schema_start = '\treturn Type.Object({\n\t\taction: stringEnum(capabilities.actions),'
    schema_end = '\t\trequest: Type.Optional(BrowserActSchema)\n\t});\n}'
    if any(text.count(part) != 1 for part in (start, schema_start, schema_end)):
        raise ValueError('Pinned browser patch seam changed')
    first = text.index(start)
    last = text.index('\n//#endregion', first)
    text = (f'import {{ projectProtectedBrowserSchema, PROTECTED_BROWSER_DESCRIPTION }} from "{helper_url}";\n'
            + text[:first] + start + '\treturn PROTECTED_BROWSER_DESCRIPTION;\n}' + text[last:])
    text = text.replace(schema_start, '\treturn projectProtectedBrowserSchema(Type.Object({\n\t\taction: stringEnum(capabilities.actions),', 1)
    text = text.replace(schema_end, '\t\trequest: Type.Optional(BrowserActSchema)\n\t}));\n}', 1)
    return text.encode()
