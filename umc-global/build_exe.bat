@echo off
setlocal
cd /d "%~dp0"

rem Constroi dist\UMCglobal.exe - launcher portatil one-file.
rem Usa o python_embeded do portable. pyinstaller e a unica dependencia de
rem build, instalada so na primeira vez.
rem Nota: sem blocos if(...)-else no bat - o cmd quebra-se com parenteses
rem soltos dentro dos blocos.

set "PY=..\python_embeded\python.exe"
if not exist "%PY%" goto :nopy

rem --- pyinstaller: dependencia de build apenas -----------------------------
"%PY%" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 "%PY%" -m pip install pyinstaller
if errorlevel 1 goto :fail

rem --- build (src + DLLs nativas embutidas) ---------------------------------
"%PY%" -m PyInstaller --noconfirm --clean --onefile --name UMCglobal --add-data "src;src" --add-data "build;build" --distpath dist --workpath build_tmp src\exe_main_global.py
if errorlevel 1 goto :fail

echo.
echo OK: %cd%\dist\UMCglobal.exe
echo Corre-o de qualquer sitio: dist\UMCglobal.exe [--umc-vram N] meu_script.py [args...]
exit /b 0

:nopy
echo [ERRO] nao encontrei %PY% - corre este bat a partir da pasta umc-global
exit /b 1

:fail
echo [ERRO] build falhou
exit /b 1
