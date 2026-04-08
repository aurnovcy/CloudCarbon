#!/bin/bash
set -e
python3 -m venv venv
source venv/bin/activate
pip install -r apps/api/requirements.txt
echo "Done. Run:"
echo "  source venv/bin/activate"
echo "  cd apps/api && python -m uvicorn src.main:app --reload --port 8000"
