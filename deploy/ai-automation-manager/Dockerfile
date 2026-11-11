FROM node:22.15.0-bookworm-slim@sha256:01cbafb3480d272d73613e5e74206ec79326f4a9ebb4d089f636b53d12d87a04 AS frontend-build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM python:3.12.10-slim-bookworm@sha256:97983fa8cc88343512862c62307159a82261c3528dc025f79e5a3f7af43e50b4
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 manager \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin manager
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt
COPY backend/app.py ./app.py
COPY --from=frontend-build /src/dist ./dist
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
