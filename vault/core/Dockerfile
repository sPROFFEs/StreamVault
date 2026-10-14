FROM docker:28.5.2-cli AS docker_tools
FROM python:3.11-slim

ARG TARGETARCH

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1

# Base tooling for media handling + the Chromium runtime deps Playwright needs.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates curl gosu aria2 git openssh-client sudo \
        # Chromium runtime libs (Playwright's `install-deps` would do this)
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libx11-6 \
        libxext6 libxcb1 fonts-liberation && \
    rm -rf /var/lib/apt/lists/*

# Use the Compose plugin and Buildx from the official CLI image. The legacy
# Python docker-compose client cannot reliably recreate newer Docker engines.
COPY --from=docker_tools /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker_tools /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Headless Chromium for the cross-origin m3u8 sniffer.
# This step is heavy (~170 MB image growth) — comment it out if you
# don't need the /api/sniffer endpoints.
RUN python -m playwright install chromium && \
    # Move the browsers cache to a stable path readable by appuser.
    mv /root/.cache/ms-playwright /opt/ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN adduser --disabled-password --gecos '' appuser
RUN echo "appuser ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/appuser && chmod 0440 /etc/sudoers.d/appuser
RUN chown -R appuser:appuser /app /opt/ms-playwright

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
