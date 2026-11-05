"""Route only the owned CDP endpoint through the image-owned local adapter."""
import ast
from pathlib import Path
import sys


def patch(source):
    ast.parse(source)
    marker = '# HomeCompute fixed CDP transport'
    if marker in source:
        return source
    anchor = '    lowered = raw.lower()\n'
    if source.count(anchor) != 1 or 'def _resolve_cdp_override(cdp_url: str)' not in source:
        raise ValueError('upstream CDP resolver changed')
    replacement = '''    # HomeCompute fixed CDP transport
    if raw == "ws://172.18.0.1:18810/devtools/browser/homecompute":
        import subprocess
        env = {key: os.environ[key] for key in ("PATH", "HTTP_PROXY", "HTTPS_PROXY", "BROWSER_TOKEN") if key in os.environ}
        return subprocess.check_output(["/usr/local/bin/node", "/usr/local/lib/nemoclaw/browser-relay.mjs", "--ensure"], env=env, text=True, timeout=10).strip()
'''
    result = source.replace(anchor, replacement + anchor)
    ast.parse(result)
    return result


if __name__ == '__main__':
    path = Path(sys.argv[1])
    path.write_text(patch(path.read_text()))
