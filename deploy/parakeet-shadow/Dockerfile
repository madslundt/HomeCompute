ARG PYTORCH_BASE=nvcr.io/nvidia/pytorch@sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9
FROM ${PYTORCH_BASE}
ARG HOME_COMPUTE_REVISION
LABEL org.opencontainers.image.source="https://github.com/madslundt/HomeCompute" \
      org.opencontainers.image.revision="${HOME_COMPUTE_REVISION}" \
      io.homecompute.source-revision="${HOME_COMPUTE_REVISION}" \
      io.homecompute.parakeet.scope="isolated-shadow-only" \
      io.homecompute.parakeet.model-snapshot="541d1f99c6b0c3cd0b11a95167540bb8edefd82b" \
      io.homecompute.parakeet.model-sha256="3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11" \
      io.homecompute.parakeet.runtime-commit="446a570e62b04a89ea001604ada7366baac2ea67"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    CUBLAS_WORKSPACE_CONFIG=:4096:8 NUMBA_CACHE_DIR=/tmp/numba \
    HOME_COMPUTE_REVISION=${HOME_COMPUTE_REVISION}
WORKDIR /opt/homecompute
COPY requirements.lock model-manifest.json model_runtime.py /opt/homecompute/
# Never resolve/replace the vendor Torch, torchvision, CUDA or their dependencies.
RUN python3 -c 'import importlib.metadata as m,platform,re,sys; assert platform.machine()=="aarch64"; assert sys.version_info[:2]==(3,12); assert m.version("torch")=="2.11.0a0+eb65b36914.nv26.2"; assert m.version("torchvision")=="0.25.0a0+1e53952f.nv26.2.44259020"' \
 && python3 -m pip install --no-cache-dir --only-binary=:all: --no-deps --require-hashes -r requirements.lock \
 && python3 -c 'import model_runtime; model_runtime.validate_environment()' \
 && CUDA_VISIBLE_DEVICES="" python3 -c 'import torch; assert not torch.cuda.is_initialized(); from transformers import ParakeetForTDT, AutoProcessor, ParakeetFeatureExtractor; assert ParakeetForTDT.__module__=="transformers.models.parakeet.modeling_parakeet"; assert ParakeetFeatureExtractor.__module__=="transformers.models.parakeet.feature_extraction_parakeet"; assert AutoProcessor.__module__=="transformers.models.auto.processing_auto"; assert not torch.cuda.is_initialized()' \
 && python3 -c 'import os,re; assert re.fullmatch("[0-9a-f]{40}", os.environ["HOME_COMPUTE_REVISION"])' \
 && groupadd --gid 10001 hc-parakeet \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin hc-parakeet \
 && mkdir -p /models \
 && chmod 0555 /opt/homecompute /models \
 && chmod 0444 /opt/homecompute/requirements.lock /opt/homecompute/model-manifest.json \
 && chmod 0555 /opt/homecompute/model_runtime.py
COPY adapter.py /opt/homecompute/adapter.py
RUN chmod 0555 /opt/homecompute/adapter.py
USER 10001:10001
ENTRYPOINT ["python3", "-I", "-B", "/opt/homecompute/adapter.py"]
