# qwen-llm-gke

Qwen2.5-0.5B-Instruct, served by llama.cpp's `llama-server`, running on
GKE. This replaces the old VPS setup (`llm-testing-deploy`'s systemd
unit) as part of [bradjobe-dev-infra](https://github.com/ScradFTW/bradjobe-dev-infra)'s
migration to GCP — see that repo for the cluster/node pool Terraform and
the overall migration plan.

Served at `llm.bradjobe.dev` via its own GKE-native Ingress (`k8s/ingress.yaml`)
rather than through the main load balancer that fronts everything else —
see bradjobe-dev-infra's README for why.

## ⚠️ Currently running CPU-only, temporarily

The design here is GPU inference on a dedicated 2-node Spot T4 pool
(`Dockerfile`, `k8s/deployment.yaml`, `-ngl 99`) — but this project's GPU
quotas (both the global "GPUs (all regions)" quota and the regional
"Preemptible NVIDIA T4 GPUs" quota) are still at their fresh-project
defaults pending Google's manual approval, with no fixed timeline.

Rather than leave the demo dark, `cloudbuild.yaml`'s `_DOCKERFILE` and
`_DEPLOYMENT_MANIFEST` substitutions currently point at `Dockerfile.cpu`
and `k8s/deployment-cpu.yaml` instead — same model, CPU-only (`-ngl 0`,
what the VPS ran), on a 4-node Spot `e2-small` pool
(bradjobe-dev-infra's `llm_cpu` node pool) that needs no GPU quota at
all. 4 nodes rather than the GPU pool's floor of 2 also doubles as a
visible multi-node GKE demo in the meantime.

**To swap back once both GPU quotas are approved:**
1. In bradjobe-dev-infra: set `enable_llm_gpu_pool = true`, push. This
   creates the GPU pool (leaves `llm_cpu` in place unless you also
   remove it).
2. In this repo's `cloudbuild.yaml`: change `_DOCKERFILE` to `Dockerfile`
   and `_DEPLOYMENT_MANIFEST` to `k8s/deployment.yaml`, push.
3. Optionally remove `google_container_node_pool.llm_cpu` from
   bradjobe-dev-infra and delete `Dockerfile.cpu` /
   `k8s/deployment-cpu.yaml` here once you're done with the CPU fallback
   — nothing depends on keeping them around.

## Layout

- `Dockerfile` — builds llama.cpp `b10819` with `GGML_CUDA=ON`, bakes in
  the Q4_K_M quantized model (downloaded from Qwen's official HF repo at
  build time, not committed to git — it's ~470MB and GitHub's non-LFS
  limit is 100MB). `Dockerfile.cpu` is the same build without CUDA,
  `-ngl 0` — currently the active one, see above.
- `k8s/` — Namespace, ConfigMap (nginx CORS sidecar config —
  llm.bradjobe.dev is a different origin from bradjobe.dev, so responses
  need real CORS headers), BackendConfig (health check + the Cloud Armor
  policy Terraform created), Service, Deployment (llama-server + the CORS
  sidecar, GPU resource request, Spot toleration), ManagedCertificate,
  Ingress. `deployment-cpu.yaml` is the CPU-pool equivalent (4 replicas,
  pod anti-affinity to spread across nodes, no GPU resources) —
  currently the active one.
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
