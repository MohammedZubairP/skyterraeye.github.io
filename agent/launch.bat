@echo off
title OrthoForge — Local Agent
echo.
echo  ==========================================
echo   OrthoForge — Starting local agent...
echo  ==========================================
echo.

REM Try conda environment first
where conda >nul 2>&1
if %errorlevel% == 0 (
    echo  Found conda. Activating ortho environment...
    call conda activate ortho 2>nul || call conda activate base
    python agent.py
    goto end
)

REM Try plain python
where python >nul 2>&1
if %errorlevel% == 0 (
    echo  Using system Python...
    python agent.py
    goto end
)

echo  ERROR: Python not found.
echo  Install Anaconda from https://www.anaconda.com
echo  Then run:  conda create -n ortho python=3.11
echo             conda install -c conda-forge gdal pillow numpy
echo             pip install flask opencv-python-headless
pause

:end
