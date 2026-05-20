#!/bin/bash
set -e
source .venv/bin/activate
export FLASK_APP="app:create_app"
flask run --port 5000
