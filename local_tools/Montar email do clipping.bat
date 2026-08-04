@echo off
chcp 65001 >nul
title Montar e-mail do clipping
python "%~dp0montar_email.py" %*
if errorlevel 1 pause
