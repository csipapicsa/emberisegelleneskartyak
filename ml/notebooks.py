"""Run experiment notebooks (notebooks/*.ipynb) from the command line, e.g. in the background.

    python -m ml.notebooks list
    python -m ml.notebooks run 01_bilinear                    # one notebook (name or number prefix)
    python -m ml.notebooks run all -p embedding=granite       # every notebook, with a parameter
    nohup venv-ml/bin/python -m ml.notebooks run all > ml-data/notebook-runs/all.log 2>&1 &

Each notebook runs top to bottom with papermill. Its "parameters" cell can be overridden with -p name=value
(the value is read as Python: numbers, True/False, None and lists work; anything else stays text). The
executed copy, with every output, goes to ml-data/notebook-runs/<time>_<name>.ipynb. The notebook in git
stays without outputs. Runs on idle CPU only (ml.lower_priority), like the training cycle, so the game
always comes first.
"""
from __future__ import annotations

import argparse
import ast
import sys
import time
from pathlib import Path

from ml import DATA_DIR, lower_priority

NOTEBOOK_DIR = Path(__file__).resolve().parents[1] / "notebooks"
RUNS_DIR = DATA_DIR / "notebook-runs"


def available(folder: Path = NOTEBOOK_DIR) -> list[Path]:
    return sorted(folder.glob("[0-9]*.ipynb"))


def resolve(names: list[str], folder: Path = NOTEBOOK_DIR) -> list[Path]:
    """Notebook paths for names: "all", a full stem ("01_bilinear") or a unique prefix ("01")."""
    notebooks = available(folder)
    if names == ["all"]:
        return notebooks
    out = []
    for name in names:
        matches = [p for p in notebooks if p.stem == name] or [p for p in notebooks if p.stem.startswith(name)]
        if len(matches) != 1:
            found = ", ".join(p.stem for p in matches) or "none"
            raise ValueError(f"{name!r} matches {found}; choose from: {', '.join(p.stem for p in notebooks)}")
        out.append(matches[0])
    return out


def parse_params(pairs: list[str]) -> dict:
    """["rank=16", "embedding=e5", "ranks=[4, 16]"] -> {"rank": 16, "embedding": "e5", "ranks": [4, 16]}."""
    params = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name.isidentifier():
            raise ValueError(f"parameter {pair!r}: expected name=value")
        try:
            params[name] = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            params[name] = value
    return params


def run(notebook: Path, params: dict, out_dir: Path = RUNS_DIR) -> Path:
    import papermill

    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{time.strftime('%Y-%m-%d_%H%M%S')}_{notebook.stem}.ipynb"
    papermill.execute_notebook(str(notebook), str(output), parameters=params, cwd=str(notebook.parent.parent),
                               kernel_name="python3", progress_bar=False, log_output=True)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the notebooks")
    run_parser = sub.add_parser("run", help="run notebooks top to bottom")
    run_parser.add_argument("notebooks", nargs="+", help='names, number prefixes or "all"')
    run_parser.add_argument("-p", "--param", action="append", default=[], metavar="NAME=VALUE",
                            help="override a value in the notebook's parameters cell (repeatable)")
    args = parser.parse_args(argv)
    if args.command == "list":
        for p in available():
            print(p.stem)
        return 0
    try:
        notebooks, params = resolve(args.notebooks), parse_params(args.param)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"CPU priority: {lower_priority()}", flush=True)
    failed = []
    for notebook in notebooks:
        print(f"== {notebook.stem} {params or ''}", flush=True)
        started = time.monotonic()
        try:
            output = run(notebook, params)
        except Exception as exc:  # noqa: BLE001 - one failing notebook shouldn't stop the others
            failed.append(notebook.stem)
            print(f"!! {notebook.stem} failed: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}",
                  file=sys.stderr, flush=True)
            continue
        print(f"   done in {time.monotonic() - started:.0f}s -> {output}", flush=True)
    if failed:
        print(f"Failed: {', '.join(failed)} (the executed copy in {RUNS_DIR} shows where)", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
