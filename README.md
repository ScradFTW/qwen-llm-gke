# qwen-llm-gke

Qwen2.5-0.5B-Instruct, served by llama.cpp's `llama-server` built with CUDA
support, running on GKE on a dedicated 2-node Spot T4 pool. This replaces
the old VPS setup (`llm-testing-deploy`'s systemd unit, CPU-only, `-ngl 0`)
as part of [bradjobe-dev-infra](https://github.com/ScradFTW/bradjobe-dev-infra)'s
migration to GCP — see that repo for the cluster/node pool Terraform and
the overall migration plan.

Served at `llm.bradjobe.dev` via its own GKE-native Ingress (`k8s/ingress.yaml`)
rather than through the main load balancer that fronts everything else —
see bradjobe-dev-infra's README for why.

## Layout

- `Dockerfile` — builds llama.cpp `b10819` with `GGML_CUDA=ON`, bakes in
  the Q4_K_M quantized model (downloaded from Qwen's official HF repo at
  build time, not committed to git — it's ~470MB and GitHub's non-LFS
  limit is 100MB).
- `k8s/` — Namespace, ConfigMap (nginx CORS sidecar config —
  llm.bradjobe.dev is a different origin from bradjobe.dev, so responses
  need real CORS headers), BackendConfig (health check + the Cloud Armor
  policy Terraform created), Service, Deployment (llama-server + the CORS
  sidecar, GPU resource request, Spot toleration), ManagedCertificate,
  Ingress.
- `cloudbuild.yaml` — builds + pushes the image, then `kubectl apply`s
  everything in `k8s/` against `bradjobe-llm-cluster`.

## Why GPU inference here and not on the VPS

The VPS ran this model CPU-only (`-ngl 0`) because it had no GPU. On a T4
node, `-ngl 99` offloads every transformer layer to the GPU — noticeably
faster generation, and the actual reason this demo is worth running on
GKE with a GPU node pool rather than just another Cloud Run service.

## Local testing

```sh
docker build -t qwen-llm-gke .
docker run --gpus all -p 8081:8081 qwen-llm-gke
curl -X POST http://localhost:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":50}'
```

Needs a local machine with an NVIDIA GPU + the NVIDIA Container Toolkit;
there's no CPU-only fallback path in this image (the whole point is GPU
inference — see above).
