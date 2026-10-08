@echo off
title ComfyUI + UMC v3 (tecnicas Strata) - TESTE
echo ============================================
echo    ComfyUI + UMC v3 - teste (pasta UMC_v3_strata)
echo ============================================

set "BASE=%~dp0"
set "PYTHON_EXE=%BASE%python_embeded\python.exe"
set "COMFY_DIR=%BASE%ComfyUI"
set "UMC3_SRC=%BASE%UMC_v3_strata\src"

"%PYTHON_EXE%" "%UMC3_SRC%\start_umc_v3.py" --umc-vram 48 --umc-ram 48 --umc-pagefile 40 --umc-priority balanced --comfyui-dir "%COMFY_DIR%" --enable-manager --windows-standalone-build --use-pytorch-cross-attention --mmap-torch-files --reserve-vram 1.0 --disable-cuda-malloc

echo.
echo ComfyUI terminou.
pause
