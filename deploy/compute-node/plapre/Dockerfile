ARG PLAPRE_BASE_IMAGE=nvcr.io/nvidia/vllm@sha256:604e5b052d1ce1b87952c72bf95cc637192c0051fc15a32886dff20bffd5c514

FROM ${PLAPRE_BASE_IMAGE} AS builder

ARG PLAPRE_CODE_REVISION=b111239d3b099cfafcb54b3471a8e9e9ba71ae8b
ARG PLAPRE_MODEL_REVISION=007e0b471e377dd5061786f7df6ad659d15c4d5f
ARG KANADE_CODE_REVISION=961f20bf892c59f391d0b6c5f7b88e70ed919b99
ARG KANADE_MODEL_REVISION=cb2c8f10959ff0e5d3e97c9b82fcc3779c9532a5
ARG HIFT_MODEL_REVISION=eec1ae6c79877dbd9379285cf8789c9e0879293d
ARG TORCHAUDIO_CODE_REVISION=34c52a67e8941bbd8e6adaca0eb0b9eabec11d78

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates libsndfile1 build-essential \
    && python3 -m venv --system-site-packages /opt/plapre/venv \
    && /opt/plapre/venv/bin/pip install --no-cache-dir \
        setuptools==80.10.2 wheel==0.46.3 ninja==1.13.0 \
        hatchling==1.27.0 uv-build==0.9.18 \
        num2words==0.5.14 docopt==0.6.2 soundfile==0.14.0 scipy==1.18.1 \
        jsonargparse==4.52.0 docstring-parser==0.18.0 typeshed-client==2.13.0 \
        wyoming==1.10.2 \
    && git clone https://github.com/pytorch/audio.git /opt/build/torchaudio \
    && git -C /opt/build/torchaudio checkout --detach "${TORCHAUDIO_CODE_REVISION}" \
    && test "$(git -C /opt/build/torchaudio rev-parse HEAD)" = "${TORCHAUDIO_CODE_REVISION}" \
    && BUILD_VERSION=2.11.0+nv26.2 USE_CUDA=0 BUILD_RNNT=0 BUILD_ALIGN=0 \
        BUILD_CUDA_CTC_DECODER=0 MAX_JOBS=4 \
        /opt/plapre/venv/bin/pip install --no-cache-dir --no-build-isolation --no-deps /opt/build/torchaudio \
    && git clone https://github.com/syv-ai/plapre.git /opt/build/plapre \
    && git -C /opt/build/plapre checkout --detach "${PLAPRE_CODE_REVISION}" \
    && test "$(git -C /opt/build/plapre rev-parse HEAD)" = "${PLAPRE_CODE_REVISION}" \
    && git clone https://github.com/frothywater/kanade-tokenizer.git /opt/build/kanade \
    && git -C /opt/build/kanade checkout --detach "${KANADE_CODE_REVISION}" \
    && test "$(git -C /opt/build/kanade rev-parse HEAD)" = "${KANADE_CODE_REVISION}"

COPY deploy/compute-node/plapre/plapre-offline.patch /opt/build/plapre-offline.patch
COPY deploy/compute-node/plapre/kanade-offline.patch /opt/build/kanade-offline.patch
RUN git -C /opt/build/plapre apply --check /opt/build/plapre-offline.patch \
    && git -C /opt/build/plapre apply /opt/build/plapre-offline.patch \
    && git -C /opt/build/kanade apply --check /opt/build/kanade-offline.patch \
    && git -C /opt/build/kanade apply /opt/build/kanade-offline.patch \
    && /opt/plapre/venv/bin/pip install --no-cache-dir --no-deps /opt/build/kanade \
    && /opt/plapre/venv/bin/pip install --no-cache-dir --no-deps /opt/build/plapre

RUN PLAPRE_MODEL_REVISION="${PLAPRE_MODEL_REVISION}" \
    KANADE_MODEL_REVISION="${KANADE_MODEL_REVISION}" \
    HIFT_MODEL_REVISION="${HIFT_MODEL_REVISION}" \
    /opt/plapre/venv/bin/python - <<'PY'
import os
from huggingface_hub import hf_hub_download, snapshot_download

snapshot_download(
    repo_id="syvai/plapre-nano-v2",
    revision=os.environ["PLAPRE_MODEL_REVISION"],
    local_dir="/opt/plapre/models/plapre",
)
snapshot_download(
    repo_id="frothywater/kanade-25hz-clean",
    revision=os.environ["KANADE_MODEL_REVISION"],
    local_dir="/opt/plapre/models/kanade",
)
hf_hub_download(
    repo_id="FunAudioLLM/CosyVoice2-0.5B",
    filename="hift.pt",
    revision=os.environ["HIFT_MODEL_REVISION"],
    local_dir="/opt/plapre/models/cosyvoice",
)
PY

RUN echo 'e731a53b7cc17e8fe31c458c5f6d0f740a182f6d64d388a2cba16d19553aa0c4  /opt/plapre/models/plapre/model.safetensors' | sha256sum -c - \
    && echo '61750ec7783fa48bbe9f6b6600e4687696df02bd6ab6800b6289392abedc96f7  /opt/plapre/models/plapre/speaker_proj.pt' | sha256sum -c - \
    && echo '56a686c93dd2bc55108d9ddd3556cb25bd8028179a058bfd2f5857c6a204566e  /opt/plapre/models/kanade/model.safetensors' | sha256sum -c - \
    && echo '3386cc880324d4e98e05987b99107f49e40ed925b8ecc87c1f4939432d429879  /opt/plapre/models/cosyvoice/hift.pt' | sha256sum -c -

ENV TORCH_HOME=/opt/plapre/torch
RUN /opt/plapre/venv/bin/python - <<'PY'
from torchaudio.pipelines import WAVLM_BASE_PLUS
WAVLM_BASE_PLUS.get_model()
PY

COPY deploy/compute-node/plapre/wyoming_adapter.py /opt/plapre/bin/wyoming_adapter.py
COPY deploy/compute-node/plapre/plapre_health_check.py /opt/plapre/bin/plapre_health_check.py
COPY deploy/compute-node/plapre/wyoming_health_check.py /opt/plapre/bin/wyoming_health_check.py
COPY scripts/tcp-edge-proxy.py /opt/plapre/bin/tcp-edge-proxy.py

RUN --network=none /opt/plapre/venv/bin/python - <<'PY'
import torch
import torchaudio
import vllm
import kanade_tokenizer
import plapre
import wyoming
assert torch.__version__.startswith("2.11.0a0+") and ".nv26.0" in torch.__version__, torch.__version__
assert torchaudio.__version__ == "2.11.0+nv26.2", torchaudio.__version__
assert vllm.__version__.startswith("0.15.1"), vllm.__version__
PY

FROM ${PLAPRE_BASE_IMAGE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/plapre /opt/plapre
ENV PATH=/opt/plapre/venv/bin:${PATH} \
    TORCH_HOME=/opt/plapre/torch \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /opt/plapre
