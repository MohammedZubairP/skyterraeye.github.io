#!/bin/bash
echo ""
echo " =========================================="
echo "  OrthoForge — Starting local agent..."
echo " =========================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Try conda environment
if command -v conda &>/dev/null; then
    echo " Found conda. Activating ortho environment..."
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate ortho 2>/dev/null || conda activate base
    python agent.py
    exit 0
fi

# Try python3
if command -v python3 &>/dev/null; then
    echo " Using system python3..."
    python3 agent.py
    exit 0
fi

echo " ERROR: Python not found."
echo " Install Anaconda from https://www.anaconda.com"
echo " Then run:"
echo "   conda create -n ortho python=3.11"
echo "   conda install -c conda-forge gdal pillow numpy"
echo "   pip install flask opencv-python-headless"
read -p "Press Enter to exit..."
