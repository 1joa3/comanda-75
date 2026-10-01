@echo off
:: ============================================================
::  Instala o AutoComanda para iniciar com o Windows
::  Execute este script UMA VEZ como Administrador no PDV.
:: ============================================================

set "EXE_PATH=%~dp0AutoComanda.exe"
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP_FOLDER%\AutoComanda.lnk"

echo.
echo ============================================================
echo   Instalador de Inicializacao Automatica - AutoComanda
echo ============================================================
echo.
echo Executavel: %EXE_PATH%
echo Atalho em : %SHORTCUT%
echo.

:: Verifica se o executavel existe
if not exist "%EXE_PATH%" (
    echo ERRO: AutoComanda.exe nao encontrado na pasta atual.
    echo Certifique-se de executar este script de dentro da pasta AutoComanda.
    pause
    exit /b 1
)

:: Cria o atalho via PowerShell (funciona em Windows 7+)
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%EXE_PATH%'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'AutoComanda - Ordem de Producao'; $s.Save()"

if %ERRORLEVEL% equ 0 (
    echo.
    echo [OK] Atalho criado com sucesso!
    echo O AutoComanda sera iniciado automaticamente no proximo login.
    echo.
) else (
    echo.
    echo [ERRO] Falha ao criar o atalho. Tente executar como Administrador.
    echo.
)

pause
