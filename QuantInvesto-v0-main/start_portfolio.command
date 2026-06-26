#!/bin/zsh
cd "$(dirname "$0")"
PORT_VALUE="${PORT:-8765}"
(sleep 1; open "http://127.0.0.1:${PORT_VALUE}/") &
python3 app.py "${PORT_VALUE}"
