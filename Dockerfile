# syntax=docker/dockerfile:1

# Comments are provided throughout this file to help you get started.
# If you need more help, visit the Dockerfile reference guide at
# https://docs.docker.com/go/dockerfile-reference/

# Want to help us make this template better? Share your feedback here: https://forms.gle/ybq9Krt8jtBL3iCk7

ARG PYTHON_VERSION=3.11.2
FROM python:${PYTHON_VERSION}-slim as base

# the OCI source label is required for the remote Docker deployment to pull the image from a registry (GHCR).
LABEL org.opencontainers.image.source=https://github.com/Mattshanevdberg/Enduro_Tracker_WebApp

# Prevents Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1

# Keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Create a non-privileged user that the app will run under.
# See https://docs.docker.com/go/dockerfile-user-best-practices/
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

# Seed the runtime profile-image directory with appuser ownership. Docker copies
# these permissions into a newly created named volume, allowing the non-root web
# process to write uploads without granting broad filesystem permissions.
RUN mkdir -p /var/lib/enduro-tracker/profile-images \
    && chown -R appuser:appuser /var/lib/enduro-tracker

# Download dependencies as a separate step to take advantage of Docker's caching.
# Leverage a cache mount to /root/.cache/pip to speed up subsequent builds.
# Leverage a bind mount to requirements.txt to avoid having to copy them into
# into this layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    python -m pip install -r requirements.txt

# Switch to the non-privileged user to run the application.
USER appuser

# Copy the source code into the container.
COPY . .

# Expose the port that the application listens on.
EXPOSE 8000

# Run the Flask application through Gunicorn using the exported app object from
# src.main. This matches the WSGI entry point the remote Docker deployment will
# use, and it avoids relying on the development-only app.run(...) block.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "src.main:app"]
