#!/bin/bash
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
cd /c/Users/jerry/ForgeAi/backend
exec python -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info
