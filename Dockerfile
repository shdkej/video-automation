# REEL ROOM 웹 UI 프리빌트 이미지 (linux/arm64 — Oracle A1 노드용)
# 파드 부팅마다 apt/pip/npm/모델 다운로드를 반복하지 않도록 전부 이미지에 굽는다.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs npm chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt

# Whisper medium 모델(~1.5G)을 이미지에 포함 — 재시작마다 재다운로드 방지.
# faster-whisper의 "medium"은 이 HF 레포로 해석된다.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-medium')"

COPY remotion-map/package.json remotion-map/package-lock.json remotion-map/
RUN cd remotion-map && npm ci --no-audit --no-fund

COPY . .

ENV REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
