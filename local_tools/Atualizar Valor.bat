@echo off
chcp 65001 >nul
title Atualizar Valor
echo.
echo   Lendo a sua sessao do Valor ja logada no Chrome (sem abrir navegador, sem senha)...
echo.
python "%~dp0refresh_valor.py"
echo.
echo   Terminou. Aperte qualquer tecla para fechar.
pause >nul
