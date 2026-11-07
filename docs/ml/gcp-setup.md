# GCP setup for cloud training

Milestone 3 of the [implementation plan](implementation-plan.md): the training image runs as a Cloud Run Job
in GCP and logs to the self-hosted MLflow, like the nightly run on the server. Everything below was run
once in Cloud Shell; Terraform for the same resources is a later step.

| | |
|---|---|
| Project | `emberisegelleneskartya` (no organization), billing on, budget alert at a few euros |
| Region | `europe-west4` (Netherlands) for everything regional |
| APIs | Cloud Run Admin, Cloud Storage, Artifact Registry, Secret Manager, IAM Credentials, STS |

## Identities and what each may do

Least privilege: every permission is granted on one resource, never on the whole project.

| Who | Identity | May | On |
|---|---|---|---|
| The server (`python -m ml.cloud`) | `eek-uploader` service account (the only key) | create objects, not read, overwrite or delete them; start and watch one job | bucket `emberisegelleneskartya-ml`; job `eek-train` |
| The training job | `eek-trainer` service account | read objects; read the MLflow password (3.4) | the bucket; one secret |
| GitHub Actions | `eek-ci-pusher` service account, via Workload Identity Federation | push images | Artifact Registry repo `eek` |

The uploader can only add files, so a leaked server key can't read the gameplay data or delete anything.
Snapshot folders are named by content hash, so it never needs to overwrite. Its key lives on the server in
`~/.config/eek/gcp-uploader.json` (mode 600).

## Bucket and service accounts (3.2)

```bash
PROJECT=emberisegelleneskartya; REGION=europe-west4; BUCKET=$PROJECT-ml
gcloud storage buckets create gs://$BUCKET --location=$REGION \
    --uniform-bucket-level-access --public-access-prevention
gcloud iam service-accounts create eek-uploader --display-name="Server: uploads snapshots"
gcloud iam service-accounts create eek-trainer  --display-name="Cloud Run Job: training"
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
    --member=serviceAccount:eek-uploader@$PROJECT.iam.gserviceaccount.com --role=roles/storage.objectCreator
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
    --member=serviceAccount:eek-trainer@$PROJECT.iam.gserviceaccount.com --role=roles/storage.objectViewer
```

IAM is eventually consistent: a binding for a service account created seconds earlier can fail with
"does not exist". Wait half a minute and run it again.

## Image registry and keyless CI push (3.3)

```bash
NUM=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com
gcloud artifacts repositories create eek --repository-format=docker --location=$REGION
# Keep the 3 newest versions, delete anything older than 14 days (storage above 0.5 GB is billed)
gcloud artifacts repositories set-cleanup-policies eek --location=$REGION --policy=cleanup.json --no-dry-run

gcloud iam service-accounts create eek-ci-pusher --display-name="GitHub Actions: pushes the training image"
gcloud artifacts repositories add-iam-policy-binding eek --location=$REGION \
    --member=serviceAccount:eek-ci-pusher@$PROJECT.iam.gserviceaccount.com --role=roles/artifactregistry.writer

gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc eek-repo --location=global \
    --workload-identity-pool=github --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.workflow_ref=assertion.workflow_ref" \
    --attribute-condition="assertion.repository_id == '1448362486' && assertion.workflow_ref == 'csipapicsa/emberisegelleneskartyak/.github/workflows/ml-image.yml@refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding eek-ci-pusher@$PROJECT.iam.gserviceaccount.com \
    --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/github/attribute.repository_id/1448362486"
```

`cleanup.json`:

```json
[{"name": "keep-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
 {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "14d"}}]
```

### How the keyless push works

1. The `ML training image` workflow asks GitHub for an OIDC token (`id-token: write`). GitHub signs it, and
   it states which repository, branch and workflow file the run comes from.
2. GCP's STS checks the signature and the provider's condition: repository ID `1448362486` (the numeric
   ID, so a re-created repository with the same name doesn't match) and `ml-image.yml` on `main`. A pull
   request's token says `refs/pull/...`, so PR runs can't push.
3. It returns a Google token for `eek-ci-pusher`, valid for 20 minutes, which can only write to `eek`.

No key exists anywhere, so there's nothing to leak, rotate or revoke. What remains:

- Whoever can merge to `main` can push an image. `main` only takes PRs with passing checks (ruleset
  `protect-main`); with one maintainer there's no second reviewer, so the GitHub account (with 2FA) is
  the real key.
- A compromised third-party action could misuse the token, so `google-github-actions/auth` is pinned to a
  commit SHA, not a movable tag.
- The worst a pushed image can do is run as the training job later: read the bucket and the MLflow
  trainer password. It can't touch the game, the server or the rest of the project.

The image goes to both registries under the same tags: `ghcr.io/csipapicsa/eek-ml-train` (public) and
`europe-west4-docker.pkg.dev/emberisegelleneskartya/eek/eek-ml-train` (what Cloud Run pulls).

## The training job (3.4)

The MLflow `trainer` password goes into Secret Manager through the console (Secret Manager → New version):
pasting into a hidden `read -s` prompt in Cloud Shell is unreliable, and the value must have no trailing
newline. Then:

```bash
TRAINER=eek-trainer@$PROJECT.iam.gserviceaccount.com
gcloud secrets create mlflow-trainer-password --replication-policy=user-managed --locations=$REGION
#   ... add the value as a new version in the console ...
gcloud secrets add-iam-policy-binding mlflow-trainer-password \
    --member=serviceAccount:$TRAINER --role=roles/secretmanager.secretAccessor

IMAGE=europe-west4-docker.pkg.dev/$PROJECT/eek/eek-ml-train@sha256:<digest>   # pinned, not :main
gcloud run jobs create eek-train --region=$REGION --image=$IMAGE --service-account=$TRAINER \
    --cpu=2 --memory=4Gi --task-timeout=30m --max-retries=0 \
    --set-env-vars=MLFLOW_TRACKING_URI=https://mlflow.emberisegelleneskartyak.hu,MLFLOW_TRACKING_USERNAME=trainer \
    --set-secrets=MLFLOW_TRACKING_PASSWORD=mlflow-trainer-password:latest \
    --add-volume=name=bucket,type=cloud-storage,bucket=$BUCKET,readonly=true \
    --add-volume-mount=volume=bucket,mount-path=/gcs \
    --command=python --args=-m,ml.train,--help
```

- The image is pinned by digest: a newly pushed image runs only after `gcloud run jobs update eek-train
  --image=...@sha256:<new digest>`, a deliberate step.
- The bucket is mounted read-only; results go to MLflow only.
- A job whose secret has no version fails to deploy ("versions/latest was not found"); a wrong value
  shows up as MLflow's 401 in the job log.

Smoke test (bucket visible, MLflow login works):

```bash
gcloud run jobs execute eek-train --region=$REGION --wait \
    --args="-c,import os;import mlflow;print('bucket:'+str(os.listdir('/gcs')));print('experiments:'+str([e.name for e in mlflow.search_experiments()]))"
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="eek-train"' \
    --freshness=15m --limit=30 --format='value(textPayload)'
```

## Training from the server (3.5)

The server's identity may also start this one job with overrides, and gets its key:

```bash
UPLOADER=eek-uploader@$PROJECT.iam.gserviceaccount.com
gcloud run jobs add-iam-policy-binding eek-train --region=$REGION \
    --member=serviceAccount:$UPLOADER --role=roles/run.jobsExecutorWithOverrides
gcloud run jobs add-iam-policy-binding eek-train --region=$REGION \
    --member=serviceAccount:$UPLOADER --role=roles/run.viewer
gcloud iam service-accounts keys create ~/eek-uploader.json --iam-account=$UPLOADER
cloudshell download ~/eek-uploader.json    # then into ~/.config/eek/gcp-uploader.json on the server, chmod 600
shred -u ~/eek-uploader.json
```

Then, on the server: `venv-ml/bin/python -m ml.cloud` (see [pipeline.md](pipeline.md#training-in-gcp)).

