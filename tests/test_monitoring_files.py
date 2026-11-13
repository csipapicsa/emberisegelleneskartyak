"""The monitoring files in deploy/monitoring stay consistent: the dashboard JSON is what its generator makes,
and every alert has a runbook entry. (The rules themselves are tested by promtool in CI.)"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "deploy" / "monitoring"


def _generator():
    spec = importlib.util.spec_from_file_location("make_dashboard", MONITORING / "make_dashboard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dashboard_json_matches_its_generator():
    committed = json.loads((MONITORING / "eek-dashboard.json").read_text())
    assert committed == _generator().DASHBOARD, "run: python3 deploy/monitoring/make_dashboard.py"


def test_dashboard_panels_have_unique_ids_and_use_the_server_prometheus():
    panels = _generator().DASHBOARD["panels"]
    assert len({p["id"] for p in panels}) == len(panels)
    assert all(p["datasource"]["uid"] == "server-prometheus" for p in panels)


def test_every_alert_has_a_runbook_entry():
    rules = (MONITORING / "eek.rules.yml").read_text()
    runbook = (ROOT / "docs" / "runbooks" / "monitoring.md").read_text()
    alerts = re.findall(r"^\s*- alert: (\w+)", rules, re.M)
    linked = re.findall(r"^\s*runbook: (\w+)", rules, re.M)
    assert alerts and len(alerts) == len(linked)
    for name in linked:
        assert re.search(rf"^### {name}$", runbook, re.M), f"docs/runbooks/monitoring.md has no '### {name}'"
