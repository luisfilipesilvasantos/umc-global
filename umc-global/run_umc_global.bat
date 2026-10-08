@echo off
setlocal
rem UMC global - arranca qualquer script Python com o UMC activo.
rem Uso: run_umc_global.bat [--umc-vram N] [--umc-ram N] [--umc-pagefile N] meu_script.py [args...]
rem Ex.: run_umc_global.bat C:\apps\meu_gradio\app.py --port 7860
set "HERE=%~dp0"

set "PY=%HERE%..\python_embeded\python.exe"
if exist "%PY%" goto :havepy
set "PY=%HERE%python_embeded\python.exe"
if exist "%PY%" goto :havepy

set "PY="
for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
:havepy
if not defined PY goto :nopy

"%PY%" "%HERE%src\run_with_umc.py" %*
exit /b %ERRORLEVEL%

:nopy
echo [UMC global] ERRO: python nao encontrado (nem python_embeded junto a esta pasta, nem PATH)
exit /b 1
