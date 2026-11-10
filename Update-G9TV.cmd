@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "..\..\.venv\Scripts\python.exe" (
 "..\..\.venv\Scripts\python.exe" g9_update.py %*
) else if exist "..\..\..\python_embeded\python.exe" (
 "..\..\..\python_embeded\python.exe" g9_update.py %*
) else (
 python g9_update.py %*
)
pause
