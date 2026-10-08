@echo off
setlocal
cd /d "%~dp0"

rem Constroi dist\UMCv3.exe - launcher portatil one-file.
rem Usa o python_embeded do portable. pyinstaller e a unica dependencia de
rem build, instalada so na primeira vez; o ComfyUI nunca a importa.
rem Nota: sem blocos if(...)-else no bat - o cmd quebra-se com parenteses
rem soltos dentro dos blocos.

set "PY=..\python_embeded\python.exe"
if not exist "%PY%" goto :nopy

rem --- pyinstaller: dependencia de build apenas -----------------------------
"%PY%" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 "%PY%" -m pip install pyinstaller
if errorlevel 1 goto :fail

rem --- build ----------------------------------------------------------------
"%PY%" -m PyInstaller --noconfirm --clean --onefile --name UMCv3 --add-data "src;src" --distpath dist --workpath build_tmp src\exe_main.py
if errorlevel 1 goto :fail

echo.
echo OK: %cd%\dist\UMCv3.exe
echo Copia o exe para a raiz de qualquer install portable ComfyUI e corre-o.
exit /b 0

:nopy
echo [ERRO] nao encontrei %PY% - corre este bat a partir da pasta UMC_v3_strata
exit /b 1

:fail
echo [ERRO] build falhou
exit /b 1
