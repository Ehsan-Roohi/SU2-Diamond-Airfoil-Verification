#!/usr/bin/env python3
"""Fail on high-confidence fatal/invalid-state messages in solver logs."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PATTERNS = [
    re.compile(r'\bsegmentation fault\b', re.I),
    re.compile(r'\bfatal\b', re.I),
    re.compile(r'negative\s+(density|pressure|temperature)', re.I),
    re.compile(r'floating point exception', re.I),
    re.compile(r'(^|[^a-z])nan([^a-z]|$)', re.I),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('logs', nargs='+')
    a = p.parse_args()
    hits = []
    for name in a.logs:
        path = Path(name)
        text = path.read_text(encoding='utf-8', errors='replace')
        for number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                hits.append(f'{path}:{number}: {line[:240]}')
    if hits:
        print('\n'.join(hits[:50]))
        raise SystemExit('SOLVER LOG CHECK FAIL')
    print('SOLVER LOG CHECK PASS')


if __name__ == '__main__':
    main()
