#!/bin/sh
set -eu
echo 'Restoring the packaged InvestLab disclosure analyses. First startup only.'
cd /opt/investlab
sha256sum --check seed.sha256
pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --role "$RESEARCH_DB_USERNAME" --no-owner --no-acl --exit-on-error --single-transaction \
  /opt/investlab/seed.dump
touch "$PGDATA/investlab-seed-ready"
echo 'InvestLab seed restore complete.'
