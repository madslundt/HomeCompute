#!/usr/bin/env python3
"""Serve scoped context with explicitly configured local inference and worker routes."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

from assistant_context_api import DEFAULT_CONTEXT_PORT, ContextService, clients, serve
from assistant_context_tools import private_bytes
from assistant_context_briefings import LocalReasoner, load_config as load_briefings
from assistant_context_retrieval import AuthenticatedHarrierClient
from assistant_context_delivery import load_delivery_auth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--clients', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--briefings', type=Path, required=True)
    parser.add_argument('--embedding-credential', type=Path)
    parser.add_argument('--briefing-credential', type=Path)
    parser.add_argument('--enable-delegation', action='store_true')
    parser.add_argument('--delegation-config', type=Path)
    parser.add_argument('--delivery-auth', type=Path)
    parser.add_argument('--enable-delivery', action='store_true')
    parser.add_argument('--improvement-status', action='store_true')
    parser.add_argument('--port', type=int, default=DEFAULT_CONTEXT_PORT)
    args = parser.parse_args()
    try:
        from assistant_context_recovery import assert_restore_ready
        assert_restore_ready(args.state)
        configured = clients(args.clients)
        policy = json.loads(args.policy.read_text())
        briefings = load_briefings(args.briefings)
        reasoner = LocalReasoner(private_bytes(args.briefing_credential, 512).decode().strip()) if args.briefing_credential else None
        embedding = AuthenticatedHarrierClient(args.embedding_credential) if args.embedding_credential else None
        declaration = None
        if args.enable_delegation:
            from assistant_context_delegation import DelegationController, load_config
            if args.delegation_config is None:
                raise ValueError('reviewed native worker configuration required')
            declaration = load_config(args.delegation_config)
            declaration['enabled'] = True
        auth = load_delivery_auth(args.delivery_auth, now=datetime.now(timezone.utc)) if args.delivery_auth else None
        if args.enable_delivery and policy['mode'] != 'active':
            raise ValueError('active attention policy required before delivery')
        service = ContextService(args.state, policy, embedding_client=embedding, briefings=briefings,
            reasoner=reasoner, delivery_auth=auth, delivery_enabled=args.enable_delivery,
            improvement_status=args.improvement_status)
        if declaration is not None:
            service.delegation = DelegationController(args.state / 'delegation.sqlite3', declaration)
        # Recover prior accepted work without relying on a new source callback.
        for scope in {client.scope for client in configured}:
            service.review(scope)
        server = serve(service, configured, ('127.0.0.1', args.port))
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
