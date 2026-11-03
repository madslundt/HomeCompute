ARG PYTHON_BASE=python:3.12-slim-bookworm@sha256:623674a14ef93d052bb9b0de4dbf5f28eb578a35263838941cc8aa43213c1487
FROM ${PYTHON_BASE} AS build
ARG WHISPER_CPP_COMMIT=f049fff95a089aa9969deb009cdd4892b3e74916
LABEL org.opencontainers.image.source="https://github.com/ggml-org/whisper.cpp" \
      org.opencontainers.image.revision="${WHISPER_CPP_COMMIT}" \
      io.homecompute.whisper-cpp.model="openai/whisper-large-v3-f16-ggml"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git libgomp1 pkg-config \
 && rm -rf /var/lib/apt/lists/*
RUN git init /src \
 && git -C /src remote add origin https://github.com/ggml-org/whisper.cpp.git \
 && git -C /src fetch --depth=1 origin "${WHISPER_CPP_COMMIT}" \
 && git -C /src checkout --detach FETCH_HEAD \
 && test "$(git -C /src rev-parse HEAD)" = "${WHISPER_CPP_COMMIT}"
RUN cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DWHISPER_BUILD_SERVER=ON \
      -DGGML_NATIVE=OFF -DGGML_OPENMP=ON -DGGML_CUDA=OFF -DGGML_METAL=OFF \
 && cmake --build /build --config Release --target whisper-server --parallel 4

FROM ${PYTHON_BASE} AS runtime
ARG WHISPER_CPP_COMMIT=f049fff95a089aa9969deb009cdd4892b3e74916
ARG HOME_COMPUTE_REVISION
LABEL org.opencontainers.image.source="https://github.com/ggml-org/whisper.cpp" \
      org.opencontainers.image.revision="${WHISPER_CPP_COMMIT}" \
      io.homecompute.source-revision="${HOME_COMPUTE_REVISION}" \
      io.homecompute.whisper-cpp.runtime-commit="${WHISPER_CPP_COMMIT}" \
      io.homecompute.whisper-cpp.model-sha256="64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2"
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 stt \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin stt \
 && mkdir -p /opt/homecompute /models \
 && chown root:root /opt/homecompute /models \
 && chmod 0555 /opt/homecompute /models
COPY --from=build /build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=build /build/bin/lib*.so* /usr/local/lib/
COPY adapter.py contract.py verify_model.py /opt/homecompute/
ENV LD_LIBRARY_PATH=/usr/local/lib WHISPER_CPP_HOME_COMPUTE_REVISION=${HOME_COMPUTE_REVISION}
RUN chmod 0555 /usr/local/bin/whisper-server /usr/local/lib/lib*.so* \
 && chmod 0555 /opt/homecompute/adapter.py /opt/homecompute/contract.py /opt/homecompute/verify_model.py \
 && ! ldd /usr/local/bin/whisper-server | grep -q 'not found'
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python3", "-I", "-B", "/opt/homecompute/adapter.py"]
