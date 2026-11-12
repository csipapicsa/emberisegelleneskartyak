"""The computer player's deploy files agree with each other: the model names the games ask for are served by
the model service, and the addresses point where the service listens."""
from __future__ import annotations

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def settings(path: Path) -> dict[str, str]:
    """Environment=KEY=VALUE and PublishPort=... lines of a unit or drop-in file."""
    out = {}
    for line in path.read_text().splitlines():
        if m := re.match(r"Environment=(\w+)=(.*)", line):
            out[m[1]] = m[2]
        elif m := re.match(r"PublishPort=(.*)", line):
            out["PublishPort"] = m[1]
    return out


def served_models() -> dict[str, str]:
    spec = settings(DEPLOY / "containers/eek-model.container")["EEK_MODEL_URIS"]
    return dict(part.split("=", 1) for part in spec.split(","))


def test_staging_and_production_ask_for_models_the_service_serves():
    models = served_models()
    assert models["challenger"].endswith("@challenger") and models["champion"].endswith("@champion")
    staging = settings(DEPLOY / "containers/eek-staging.container")
    production = settings(DEPLOY / "systemd/emberisegellenes.service.d/computer-player.conf")
    assert staging["EEK_MODEL_NAME"] == "challenger" and production["EEK_MODEL_NAME"] == "champion"


def test_addresses_point_at_the_service():
    port = settings(DEPLOY / "containers/eek-model.container")["PublishPort"]
    assert port.startswith("127.0.0.1:"), "localhost only"
    host_port = port.split(":")[1]
    production = settings(DEPLOY / "systemd/emberisegellenes.service.d/computer-player.conf")
    assert production["EEK_MODEL_URL"] == f"http://127.0.0.1:{host_port}"
    staging = settings(DEPLOY / "containers/eek-staging.container")
    assert staging["EEK_MODEL_URL"] == "http://eek-model:8020"
    assert "ContainerName=eek-model" in (DEPLOY / "containers/eek-model.container").read_text()


# Host ports other services already use on the production server (see eek-model.container). 8020 was the model
# service's first choice and collided with the ESP32 energy API.
TAKEN_HOST_PORTS = {"5000", "8000", "8001", "8010", "8011", "8020", "8030"}


def test_the_model_service_port_is_free_on_the_server():
    host_port = settings(DEPLOY / "containers/eek-model.container")["PublishPort"].split(":")[1]
    assert host_port not in TAKEN_HOST_PORTS


def test_staging_keeps_no_gameplay_log():
    assert settings(DEPLOY / "containers/eek-staging.container")["EEK_GAMEPLAY_LOG"] == "0"
