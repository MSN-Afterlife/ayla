FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .

RUN --mount=type=cache,target=/root/.cache/pip python3 -m pip install --break-system-packages -r requirements.lock

COPY . .

EXPOSE 8090

CMD ["python3", "main.py"]
