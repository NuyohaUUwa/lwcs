@echo off
chcp 65001 >nul
title Lwcs Multi Game Launcher

cd /d "%~dp0"

call conda activate lwcs

python -m game_test.instance_tool

exit /b %errorlevel%