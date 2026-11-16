#!/bin/sh
set -eu
cd "$(dirname "$0")"
docker info >/dev/null
if [ ! -f .env ]; then
  secret() { od -An -N36 -tx1 /dev/urandom | tr -d ' \n'; }
  sed -e "s/CHANGE_ME_ADMIN/$(secret)/" -e "s/CHANGE_ME_DATABASE/$(secret)/" \
      -e "s/CHANGE_ME_SERVICE/$(secret)/" .env.example > .env
fi
if [ -f images.tar.gz ]; then
  if command -v sha256sum >/dev/null; then sha256sum -c images.sha256; else shasum -a 256 -c images.sha256; fi
  docker load --input images.tar.gz
else
  docker compose pull
fi
docker compose up --detach --wait --wait-timeout 900
printf '\nOpen http://127.0.0.1:8000/ (or the port in .env). Stop with: docker compose stop\n'
