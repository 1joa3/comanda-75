@echo off
:: ============================================================
::  Remove o AutoComanda da inicializacao automatica do Windows
:: ============================================================

set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AutoComanda.lnk"

echo.
echo ============================================================
echo   Remover Inicializacao Automatica - AutoComanda
echo ============================================================
echo.

if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo [OK] Atalho removido. O AutoComanda NAO sera mais iniciado automaticamente.
) else (
    echo [INFO] Nenhum atalho encontrado. O AutoComanda ja nao inicia automaticamente.
)

echo.
pause
