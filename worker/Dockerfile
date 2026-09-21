# Статический ffmpeg: в базовом образе Lambda (Amazon Linux 2023) его нет,
# а yt-dlp без ffmpeg/ffprobe не извлечёт аудио и не сконвертирует обложку.
FROM mwader/static-ffmpeg:8.1.2 AS ffmpeg

# JS-рантайм: свежим yt-dlp он нужен для расшифровки сигнатур плеера
# YouTube. Deno выбран потому, что yt-dlp включает его по умолчанию —
# с ним не нужен параметр js_runtimes.
FROM denoland/deno:bin-2.9.7 AS deno

FROM public.ecr.aws/lambda/python:3.12-arm64

COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/
COPY --from=deno /deno /usr/local/bin/deno
COPY --from=ghcr.io/astral-sh/uv:0.7.1 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt \
    && pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}" \
    && rm -f requirements.txt pyproject.toml uv.lock

COPY worker/app/ ${LAMBDA_TASK_ROOT}/app/

# В Lambda на запись доступен только /tmp — туда же уводим кеши
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    DENO_DIR=/tmp/deno \
    MEDIA_DIR=/tmp/media

CMD ["app.handler.handler"]
