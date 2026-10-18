@echo off
rem Run by Windows Task Scheduler (see install_schedule.ps1): one Job Hunter run, logged.
cd /d "%~dp0.."
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\scheduled.log
".venv\Scripts\python.exe" -m jobhunter run --log >> logs\scheduled.log 2>&1
