@echo off
chcp 65001 >nul
title Lwcs Game Launcher

cd /d "%~dp0"

call conda activate lwcs

python -m game_test.run

pause