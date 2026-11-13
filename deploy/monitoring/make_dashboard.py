"""Generates eek-dashboard.json, the game's Grafana dashboard (a Python source is easier to review than JSON).

Run after changing it:  python3 deploy/monitoring/make_dashboard.py
The server's Grafana loads the JSON from a file (provisioning), so changes go through a PR, not the UI.
"""
from __future__ import annotations

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "server-prometheus"}
ENV = 'env="$env"'


def panel(pid, title, kind, targets, x, y, w, h, unit="short", stack=False, desc="", defaults=None, options=None):
    p = {
        "id": pid, "type": kind, "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": chr(65 + i), "datasource": DS, "expr": e, "legendFormat": lf}
                    for i, (e, lf) in enumerate(targets)],
        "fieldConfig": {"defaults": {"unit": unit, "custom": {}, **(defaults or {})}, "overrides": []},
        "options": options or {},
    }
    if kind == "timeseries":
        p["fieldConfig"]["defaults"]["custom"] = {
            "fillOpacity": 30 if stack else 8, "lineWidth": 1, "showPoints": "never",
            "stacking": {"mode": "normal" if stack else "none"},
        }
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi", "sort": "desc"}, **(options or {})}
    elif kind == "stat":
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area",
                        "textMode": "auto", **(options or {})}
    return p


def thresholds(*steps):
    return {"thresholds": {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}}


NOT_PRINT = 'route!~"/api/print.*|/metrics"'
RANDOM_SHARE = (f'sum(increase(eek_computer_player_decisions_total{{{ENV}, decision="random"}}[1h]))'
                f' / sum(increase(eek_computer_player_decisions_total{{{ENV}}}[1h]))')

PANELS = [
    # Now
    panel(1, "Version", "stat", [(f"eek_build_info{{{ENV}}}", "{{version}}")], 0, 0, 4, 4,
          desc="The commit running (production changes only through the Promote workflow).",
          options={"textMode": "name", "graphMode": "none"}),
    panel(2, "Games in progress", "stat",
          [(f'sum(eek_rooms{{{ENV}, phase=~"submitting|revealed|round_ending"}})', "")], 4, 0, 4, 4),
    panel(3, "Players connected", "stat", [(f'eek_players{{{ENV}, kind="human_connected"}}', "")], 8, 0, 4, 4),
    panel(4, "Games started (24 h)", "stat", [(f"increase(eek_games_started_total{{{ENV}}}[24h])", "")], 12, 0, 4, 4,
          options={"graphMode": "none"}),
    panel(5, "Computer player: random (1 h)", "stat", [(RANDOM_SHARE, "")], 16, 0, 4, 4, unit="percentunit",
          desc="Share of the computer player's decisions made at random because the model service didn't answer. "
               "Alert above 20%.",
          defaults=thresholds((None, "green"), (0.05, "orange"), (0.2, "red"))),
    panel(6, "5xx (1 h)", "stat",
          [(f'sum(increase(eek_http_requests_total{{{ENV}, status=~"5.."}}[1h])) or vector(0)', "")],
          20, 0, 4, 4, defaults=thresholds((None, "green"), (1, "orange"), (10, "red")),
          options={"graphMode": "none"}),
    # Players and rooms
    panel(7, "Rooms by phase", "timeseries", [(f"eek_rooms{{{ENV}}}", "{{phase}}")], 0, 4, 12, 8, stack=True),
    panel(8, "Players and WebSockets", "timeseries",
          [(f"eek_players{{{ENV}}}", "{{kind}}"), (f"eek_websocket_connections{{{ENV}}}", "open WebSockets")],
          12, 4, 12, 8),
    # Requests
    panel(9, "Requests by route", "timeseries",
          [(f"topk(8, sum by (route) (rate(eek_http_requests_total{{{ENV}}}[5m])))", "{{route}}")],
          0, 12, 12, 8, unit="reqps"),
    panel(10, "Latency (all routes except printing)", "timeseries",
          [(f"histogram_quantile({q}, sum by (le) (rate(eek_http_request_duration_seconds_bucket{{{ENV}, {NOT_PRINT}}}[5m])))",
            f"p{int(q * 100)}") for q in (0.5, 0.95, 0.99)],
          12, 12, 12, 8, unit="s", desc="Alert when p95 is over 1 s for 15 minutes."),
    panel(11, "Responses by status", "timeseries",
          [(f"sum by (status) (rate(eek_http_requests_total{{{ENV}}}[5m]))", "{{status}}")],
          0, 20, 12, 8, unit="reqps", stack=True),
    panel(12, "Slowest routes (p95)", "timeseries",
          [(f"topk(5, histogram_quantile(0.95, sum by (route, le) (rate(eek_http_request_duration_seconds_bucket{{{ENV}}}[15m]))))",
            "{{route}}")],
          12, 20, 12, 8, unit="s"),
    # Computer player and model service
    panel(13, "Computer player decisions", "timeseries",
          [(f"sum by (decision) (increase(eek_computer_player_decisions_total{{{ENV}}}[15m]))", "{{decision}}")],
          0, 28, 12, 8, stack=True, desc="Per 15 minutes. 'random' = the model service didn't answer in time."),
    panel(14, "Model service latency and errors", "timeseries",
          [(f"histogram_quantile(0.5, sum by (le) (rate(eek_model_request_duration_seconds_bucket{{{ENV}}}[15m])))", "p50"),
           (f"histogram_quantile(0.95, sum by (le) (rate(eek_model_request_duration_seconds_bucket{{{ENV}}}[15m])))", "p95"),
           (f'sum(increase(eek_model_requests_total{{{ENV}, outcome="error"}}[15m]))', "errors / 15 min")],
          12, 28, 12, 8, unit="s"),
    # The process
    panel(15, "Game process memory", "timeseries",
          [(f"process_resident_memory_bytes{{{ENV}, job=\"eek\"}}", "resident")], 0, 36, 12, 7, unit="bytes"),
    panel(16, "Game process CPU", "timeseries",
          [(f"rate(process_cpu_seconds_total{{{ENV}, job=\"eek\"}}[5m])", "cores")], 12, 36, 12, 7),
]

DASHBOARD = {
    "uid": "eek-overview",
    "title": "EEK game",
    "description": "Emberiségellenes Kártyák: players, requests, errors, the computer player. "
                   "Source: deploy/monitoring/make_dashboard.py in the game's repo.",
    "tags": ["eek", "prometheus"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "refresh": "1m",
    "time": {"from": "now-24h", "to": "now"},
    "templating": {"list": [{
        "name": "env", "label": "Environment", "type": "custom", "query": "prod,staging",
        "current": {"text": "prod", "value": "prod"},
        "options": [{"text": e, "value": e, "selected": e == "prod"} for e in ("prod", "staging")],
    }]},
    "panels": PANELS,
}

if __name__ == "__main__":
    out = Path(__file__).with_name("eek-dashboard.json")
    out.write_text(json.dumps(DASHBOARD, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out} ({len(PANELS)} panels)")
