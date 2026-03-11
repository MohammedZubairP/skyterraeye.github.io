@echo off
title OrthoForge — First Time Setup
echo.
echo  ==========================================
echo   OrthoForge — Installing dependencies
echo  ==========================================
echo.
echo  This only needs to run once.
echo.

where conda >nul 2>&1
if not %errorlevel% == 0 (
    echo  ERROR: Anaconda/Miniconda not found.
    echo  Download from: https://www.anaconda.com/download
    pause
    exit /b 1
)

echo  [1/3] Creating conda environment...
call conda create -n ortho python=3.11 -y

echo  [2/3] Installing GDAL, numpy, pillow...
call conda install -n ortho -c conda-forge gdal=3.8 pillow numpy -y

echo  [3/3] Installing Flask and OpenCV...
call conda run -n ortho pip install flask opencv-python-headless

echo.
echo  ==========================================
echo   Setup complete! Run launch.bat to start.
echo  ==========================================
echo.
pause
