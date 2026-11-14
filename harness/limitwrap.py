#!/usr/bin/env python3
"""Process-limit wrapper executed instead of the untrusted server process.

Usage (built by harness.sandbox.wrap_argv):
    python limitwrap.py '<rlimits json>' [ns-prefix argv...] -- <server argv...>

Applies resource limits to itself, then exec()s the server command so the
limits (and the exit status) belong to the real server process.
"""
from __future__ import annotations

import json
import os
import resource
import sys


def main() -> None:
    args = sys.argv[1:]
    if "--" not in args or len(args) < 2:
        print("limitwrap: bad invocation", file=sys.stderr)
        sys.exit(2)
    sep = args.index("--")
    limits = json.loads(args[0])
    prefix = args[1:sep]
    inner = args[sep + 1:]
    if not inner:
        print("limitwrap: empty inner argv", file=sys.stderr)
        sys.exit(2)
    for name, value in limits.items():
        rlimit = getattr(resource, name, None)
        if rlimit is not None:
            try:
                resource.setrlimit(rlimit, (value, value))
            except (ValueError, OSError):
                pass  # e.g. RLIMIT_AS soft cap below current usage; never fatal
    if prefix:
        os.execvp(prefix[0], [*prefix, *inner])
    os.execvp(inner[0], inner)


if __name__ == "__main__":
    main()
