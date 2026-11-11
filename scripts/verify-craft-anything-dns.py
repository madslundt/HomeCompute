#!/usr/bin/env python3
"""Print the declared private Craft Anything record, optionally resolve it."""
import argparse
import json
from pathlib import Path
import socket

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "name": "craft-anything.home.arpa",
    "type": "A",
    "value": "192.168.30.122",
    "ttl": 300,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "config/craft-anything-dns.json").read_text())
    if manifest != {"schema_version": 1, "zone": "home.arpa", "records": [EXPECTED]}:
        raise SystemExit("Craft Anything private DNS declaration requires review")
    result = {"desired": EXPECTED, "verified": False}
    if args.check:
        try:
            observed = sorted({
                entry[4][0]
                for entry in socket.getaddrinfo(EXPECTED["name"], 443, socket.AF_INET, socket.SOCK_STREAM)
            })
        except OSError:
            observed = []
        result.update(observed=observed, verified=observed == [EXPECTED["value"]])
    print(json.dumps(result))
    return 0 if not args.check or result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
