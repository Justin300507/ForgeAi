@echo off
set PYTHONIOENCODING=utf-8
cd /d "C:\Users\jerry\onedrive\Desktop\forgeai\backend"
venv\Scripts\python.exe scripts\auto_test.py > artifacts\test_output.txt 2>&1
echo Done >> artifacts\test_output.txt
