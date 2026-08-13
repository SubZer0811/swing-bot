#!/bin/bash
set -e

cd /app

if [ -d .git ]; then
  git pull origin main
else
  echo "No git repo found; skipping pull"
fi

docker compose build swing-bot
docker compose up -d swing-bot

echo "swing-bot deployed successfully"
