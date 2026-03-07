# Cloud Run Deployment

This deploys production services with real infrastructure:

- Orchestrator on Cloud Run
- Workers on Cloud Run
- Redis (Upstash)
- Firestore Native mode
- Pub/Sub topic + push subscription
- GCS asset bucket

## 1) Prerequisites

```bash
gcloud config set project brio-488311
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  pubsub.googleapis.com \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com
```

Create Artifact Registry repo (once):

```bash
gcloud artifacts repositories create whatif \
  --repository-format=docker \
  --location=us-central1
```

Create bucket if needed:

```bash
gsutil mb -l us-central1 gs://whatif-brio-488311-assets
```

## 2) Create Pub/Sub Resources

```bash
gcloud pubsub topics create whatif-asset-jobs
```

## 3) Build and Deploy Workers

```bash
cd /Users/avichaldwivedi/dev/Whatify/services/workers
gcloud builds submit --tag us-central1-docker.pkg.dev/brio-488311/whatif/workers:latest .

gcloud run deploy whatif-workers \
  --image us-central1-docker.pkg.dev/brio-488311/whatif/workers:latest \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLOUD_PROJECT=brio-488311,GOOGLE_CLOUD_LOCATION=us-central1,WHATIF_ASSET_BUCKET=whatif-brio-488311-assets,WHATIF_IMAGE_MODEL=imagen-3.0-generate-002,WHATIF_VIDEO_MODEL=veo-2.0-generate-001
```

Capture worker URL:

```bash
WORKER_URL=$(gcloud run services describe whatif-workers --region us-central1 --format='value(status.url)')
echo "$WORKER_URL"
```

Create push subscription to worker:

```bash
gcloud pubsub subscriptions create whatif-asset-jobs-workers \
  --topic=whatif-asset-jobs \
  --push-endpoint="${WORKER_URL}/jobs/pubsub" \
  --ack-deadline=30
```

## 4) Deploy Orchestrator

Set Upstash REST credentials first:

- `UPSTASH_REDIS_REST_URL=https://...upstash.io`
- `UPSTASH_REDIS_REST_TOKEN=...`

```bash
cd /Users/avichaldwivedi/dev/Whatify/services/orchestrator
gcloud builds submit --tag us-central1-docker.pkg.dev/brio-488311/whatif/orchestrator:latest .

gcloud run deploy whatif-orchestrator \
  --image us-central1-docker.pkg.dev/brio-488311/whatif/orchestrator:latest \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars WHATIF_ENV=prod,GOOGLE_CLOUD_PROJECT=brio-488311,GOOGLE_CLOUD_LOCATION=us-central1,WHATIF_VERTEX_LOCATION=global,UPSTASH_REDIS_REST_URL=${UPSTASH_REDIS_REST_URL},UPSTASH_REDIS_REST_TOKEN=${UPSTASH_REDIS_REST_TOKEN},FIRESTORE_DATABASE="(default)",WHATIF_PUBSUB_TOPIC=whatif-asset-jobs,WHATIF_VERTEX_MODEL=gemini-3.1-flash-lite-preview,WHATIF_INTERLEAVED_MODEL=gemini-3.1-flash-image-preview,WHATIF_INTERLEAVED_FALLBACK_MODEL=gemini-2.5-flash-image,WHATIF_AGENT_MAX_ATTEMPTS=3,WHATIF_AGENT_RETRY_BASE_DELAY_SECONDS=0.8,GOOGLE_GENAI_USE_VERTEXAI=true,WHATIF_LIVE_APP_NAME=whatif-live,WHATIF_LIVE_LOCATION=us-central1,WHATIF_LIVE_MODEL=gemini-live-2.5-flash-native-audio,WHATIF_LIVE_VOICE=Aoede,WHATIF_INPUT_AUDIO_MIME=audio/pcm\\;rate=16000,WHATIF_ENABLE_AFFECTIVE_DIALOG=true,WHATIF_ENABLE_PROACTIVE_AUDIO=false,WHATIF_ORCHESTRATOR_CALLBACK_URL=https://whatif-orchestrator-xxxxx-uc.a.run.app/api/v1/assets/jobs/callback
```

After first deploy, update callback URL to the actual service URL:

```bash
ORCH_URL=$(gcloud run services describe whatif-orchestrator --region us-central1 --format='value(status.url)')

gcloud run services update whatif-orchestrator \
  --region us-central1 \
  --set-env-vars WHATIF_ORCHESTRATOR_CALLBACK_URL="${ORCH_URL}/api/v1/assets/jobs/callback"
```

## 5) Health Checks

```bash
curl -s "${ORCH_URL}/api/v1/system/health"
curl -s "${WORKER_URL}/api/v1/system/health"
```

## 6) Minimal End-to-End Smoke

```bash
curl -s -X POST "${ORCH_URL}/api/v1/session/start" \
  -H 'Content-Type: application/json' \
  -d '{"divergence_point":"What if Alexandria never burned?","tone":"cinematic","pacing":"normal"}'
```

## 7) Live WebSocket Smoke

Use any WebSocket client to connect:

`wss://<orchestrator-url>/api/v1/session/<session_id>/live?user_id=demo-user`

Send:

- text frame: `{ \"type\": \"text\", \"text\": \"continue the scene\" }`
- binary frame: PCM16 audio chunk (`audio/pcm;rate=16000`)
