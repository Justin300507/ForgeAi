@echo off
set PYTHONIOENCODING=utf-8
cd /d "C:\Users\jerry\onedrive\Desktop\forgeai\backend"
venv\Scripts\python.exe auto_test.py > test_output.txt 2>&1
echo Done >> test_output.txt
