#!/usr/bin/env python3
"""Serve the private assistant context API; no model, notification or action execution."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from assistant_context_api import ContextService, clients, serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--clients', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    args = parser.parse_args()
    try:
        configured = clients(args.clients)
        policy = json.loads(args.policy.read_text())
        service = ContextService(args.state, policy)
        # Recover prior accepted work without relying on a new source callback.
        for scope in {client.scope for client in configured}:
            service.review(scope)
        server = serve(service, configured)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    except (OSError, ValueError):
        print('Assistant context startup failed; inspect private configuration and state.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
