"""The ML pipeline for the computer player: export, validation, features, training, evaluation.

Plan: docs/ml/implementation-plan.md. Dependencies: requirements-ml.txt (not part of the game image).
"""
from __future__ import annotations

import os
from pathlib import Path

# Snapshots and the embeddings cache. Defaults to ml-data/ in the repository; the training image sets
# EEK_ML_DATA_DIR to its /data volume, because the code folder in a container is read-only.
DATA_DIR = Path(os.environ.get("EEK_ML_DATA_DIR") or Path(__file__).resolve().parents[1] / "ml-data")


def lower_priority() -> str:
    """Run this process (and the processes it starts) only on CPU time the game doesn't need.

    The pipeline shares the server with the live game, which must always come first. SCHED_IDLE gets CPU
    only when nothing else wants it; where that isn't available, nice 19 (the lowest normal priority).
    A process may always lower its own priority, so this needs no privileges. Returns what was applied.
    """
    applied = []
    try:
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
        applied.append("SCHED_IDLE")
    except (AttributeError, OSError):  # pragma: no cover - not Linux
        pass
    try:
        current = os.nice(0)
        if current < 19:
            os.nice(19 - current)
        applied.append("nice 19")
    except OSError:  # pragma: no cover
        pass
    return ", ".join(applied) or "unchanged"
