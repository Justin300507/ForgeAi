#!/usr/bin/env python3
"""
Start ForgeAI server with forced UTF-8 encoding.
Bypasses Windows cp1252 limitation by reconfiguring stdio before uvicorn starts.
"""
import sys
import os
import io

# Force UTF-8 for all I/O
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Also set environment variable for subprocess spawning
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Now start uvicorn
import subprocess
os.chdir('backend')
subprocess.run([
    sys.executable, '-m', 'uvicorn',
    'main:app',
    '--port', '8000',
    '--log-level', 'error'
])
