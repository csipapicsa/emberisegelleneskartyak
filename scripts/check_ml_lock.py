"""Check requirements-ml.txt against requirements-ml.in and requirements-dev.txt, without pip-compile.

Recompiling requirements-ml.txt (as CI does for the other lock files) would download every PyTorch wheel
on the CPU index, all platforms and Python versions, about 11 GB, only to compute hashes. Instead CI checks:

1. every package pinned in requirements-ml.in has exactly that version in requirements-ml.txt;
2. every package in both requirements-ml.txt and requirements-dev.txt has the same version there, so
   both files install together (requirements-ml.in is compiled with -c requirements-dev.txt);
3. every pin in requirements-ml.txt carries at least one hash.

    python scripts/check_ml_lock.py      # exit 1 and a list of problems if a check fails
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;#]+)")


def _name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(text: str) -> dict[str, str]:
    """name -> version for every `name==version` line (comments, options and hashes ignored)."""
    out = {}
    for line in text.splitlines():
        match = _PIN.match(line.strip())
        if match:
            out[_name(match.group(1))] = match.group(2)
    return out


def unhashed(text: str) -> list[str]:
    """Pinned packages in a lock file without a --hash line."""
    missing, current, hashed = [], None, False
    for line in text.splitlines() + ["end==0"]:
        match = _PIN.match(line)
        if match:
            if current and not hashed:
                missing.append(current)
            current, hashed = _name(match.group(1)), False
        elif "--hash=sha256:" in line:
            hashed = True
    return missing


def problems(ml_in: str, ml_txt: str, dev_txt: str) -> list[str]:
    found = []
    locked, dev = pins(ml_txt), pins(dev_txt)
    for name, version in pins(ml_in).items():
        if locked.get(name) != version:
            found.append(f"{name}: requirements-ml.in pins {version}, requirements-ml.txt has {locked.get(name)}")
    for name in sorted(locked.keys() & dev.keys()):
        if locked[name] != dev[name]:
            found.append(f"{name}: {locked[name]} in requirements-ml.txt, {dev[name]} in requirements-dev.txt")
    found += [f"{name}: no hash in requirements-ml.txt" for name in unhashed(ml_txt)]
    return found


def main(root: Path = ROOT) -> int:
    found = problems(*((root / f).read_text() for f in ("requirements-ml.in", "requirements-ml.txt",
                                                        "requirements-dev.txt")))
    for problem in found:
        print(problem, file=sys.stderr)
    if found:
        print("Regenerate requirements-ml.txt (command in requirements-ml.in).", file=sys.stderr)
        return 1
    print("requirements-ml.txt matches requirements-ml.in and requirements-dev.txt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
