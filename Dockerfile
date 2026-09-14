# GPU build, on llama.cpp's own official CUDA image — see Dockerfile.cpu
# (the currently-active one, while GPU quota is pending) for why this
# replaced a from-source multi-stage build.
FROM ghcr.io/ggml-org/llama.cpp:server-cuda

RUN curl -fL -o /model.gguf \
      "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

# -ngl 99 offloads every transformer layer to the GPU (0 on the CPU
# build). ENTRYPOINT is inherited from the base image.
CMD ["-m", "/model.gguf", "--port", "8080", "-c", "2048", "--parallel", "2", "-ngl", "99", "--no-webui"]
