#!/bin/bash

set -e

echo "[ENTRYPOINT] Preparing /saas-data..."

mkdir -p /saas-data/cache
mkdir -p /saas-data/users
mkdir -p /saas-data/downloads
mkdir -p /saas-data/transcoded

if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser /saas-data
    echo "[ENTRYPOINT] Dropping privileges to appuser..."
    exec gosu appuser "$@"
fi

exec "$@"
