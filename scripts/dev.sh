#!/bin/sh

set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
venv_dir="$project_dir/.venv"

if [ ! -x "$venv_dir/bin/python" ] || [ ! -x "$venv_dir/bin/watchfiles" ]; then
  echo "Development environment is missing. Run:"
  echo "  uv venv --python 3.11 .venv"
  echo "  uv pip install --python .venv/bin/python -r requirements-dev.txt"
  exit 1
fi

cd "$project_dir"
exec "$venv_dir/bin/watchfiles" \
  --filter python \
  "$venv_dir/bin/python bot.py" \
  .
