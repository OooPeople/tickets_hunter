ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        chromium \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirement.txt requirements-dev.txt /tmp/tickets-hunter-requirements/
RUN python -m pip install --no-cache-dir \
        -r /tmp/tickets-hunter-requirements/requirements-dev.txt \
    && rm -rf /tmp/tickets-hunter-requirements

WORKDIR /workspace
