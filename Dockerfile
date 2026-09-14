# Same llama.cpp build (b10819) and quantization (Q4_K_M) as the current
# VPS deployment, built with CUDA support (-ngl 0 on the VPS meant CPU-only
# there; on a T4 node this offloads every layer to the GPU instead).
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
      git cmake build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch b10819 https://github.com/ggml-org/llama.cpp.git .
RUN cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --config Release -j"$(nproc)" --target llama-server

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04
# libgomp1: GNU OpenMP runtime — llama.cpp's CPU-side code paths link
# against it even in a CUDA build. See Dockerfile.cpu, where this exact
# missing-library crash was caught for real.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /src/build/bin/llama-server ./llama-server
# *.so* (not *.so): llama.cpp's shared libs use SONAME versioning
# (libllama-common.so.0 etc.) — see Dockerfile.cpu, where this exact glob
# bug was caught by a real crash on the CPU build.
COPY --from=build /src/build/bin/*.so* ./

# Baked into the image rather than fetched from GCS at pod start — one
# model, rebuilt rarely, and Docker's layer cache means this ~470MB layer
# only re-downloads when this line changes, not on every code change.
# Same file Qwen publishes officially, confirmed identical size (491400032
# bytes) to the one already validated on the VPS.
RUN curl -fL -o model.gguf \
      "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

ENV LD_LIBRARY_PATH=/app
EXPOSE 8081

# Bound to loopback: only the nginx sidecar (k8s/deployment.yaml) talks to
# this directly, same "never expose the raw model server" shape as the
# VPS's nginx-in-front-of-loopback-only-llama-server setup.
ENTRYPOINT ["/app/llama-server", \
  "-m", "/app/model.gguf", \
  "--host", "127.0.0.1", \
  "--port", "8081", \
  "-c", "2048", \
  "--parallel", "2", \
  "-ngl", "99", \
  "--no-webui"]
