@echo off
start python server.py
timeout /t 2 /nobreak >nul
start http://localhost:8080
