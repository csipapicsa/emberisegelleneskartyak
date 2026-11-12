"""Production's container, timers and Promote workflow agree with each other and with nginx."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def unit(path: str) -> str:
    return (DEPLOY / path).read_text()


def values(text: str, key: str) -> list[str]:
    return re.findall(rf"^{key}=(.*)$", text, flags=re.M)


def test_production_container_takes_over_the_host_services_address():
    prod = unit("containers/eek-prod.container")
    nginx = unit("nginx/emberisegellenes.conf")
    assert values(prod, "PublishPort") == ["127.0.0.1:8010:8010"]
    assert "proxy_pass http://127.0.0.1:8010;" in nginx, "nginx needs no change"
    assert values(prod, "Image") == ["ghcr.io/csipapicsa/emberisegelleneskartyak:prod"]
    assert values(prod, "AutoUpdate") == ["registry"]


def test_production_and_staging_are_isolated_but_both_reach_the_model():
    prod, staging, model = (unit(f"containers/{n}.container") for n in ("eek-prod", "eek-staging", "eek-model"))
    assert values(prod, "Network") == ["eek-prod.network"]
    assert values(staging, "Network") == ["eek-staging.network"]
    assert set(values(model, "Network")) == {"eek-prod.network", "eek-staging.network"}
    assert "EEK_MODEL_NAME=champion" in values(prod, "Environment")
    assert "EEK_MODEL_URL=http://eek-model:8020" in values(prod, "Environment")


def test_the_data_directory_is_the_same_everywhere():
    prod = unit("containers/eek-prod.container")
    assert values(prod, "Volume") == ["/srv/eek/prod:/data:Z"]
    assert "EEK_DATA_DIR=/srv/eek/prod" in unit("systemd/eek-prod-db-backup.service")
    assert "/srv/eek/prod/generated/print" in unit("systemd/eek-prod-print-cleanup.service")
    assert "EEK_DATA_DIR=/srv/eek/prod" in unit("systemd/eek-ml-train.service.d/prod-container.conf")
    for name in ("eek-prod-db-backup", "eek-prod-print-cleanup"):
        assert f"Unit={name}.service" in unit(f"systemd/{name}.timer")
        assert "User=eek" in unit(f"systemd/{name}.service")


def test_promote_workflow_needs_an_approval_and_only_retags():
    wf = yaml.safe_load((ROOT / ".github/workflows/promote.yml").read_text())
    assert set(wf[True]) == {"workflow_dispatch"}, "manual only"   # yaml reads the key `on` as True
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["promote"]
    assert job["environment"] == "production"
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    steps = "\n".join(s.get("run", "") for s in job["steps"])
    assert "imagetools create --tag \"$REPO_IMAGE:prod\"" in steps
    assert not re.search(r"docker (buildx )?build\s", steps), "never rebuild: production runs the tested image"
    assert "^[0-9a-f]{7}$" in steps, "the commit input is validated"
    assert "${{ inputs.commit }}" not in steps, "inputs reach scripts through env, not template expansion"
