#!/bin/bash
# Run this on your DigitalOcean VPS to install all dependencies
# for the YouTube translation pipeline.
#
# Usage:
#   chmod +x install_yt_translate.sh
#   ./install_yt_translate.sh

set -e

echo "==> Installing Python packages..."
pip install yt-dlp openai-whisper deep-translator edge-tts --break-system-packages

echo ""
echo "==> Verifying ffmpeg (already required by RSI Vault)..."
if ! command -v ffmpeg &>/dev/null; then
  echo "  ffmpeg not found — installing..."
  sudo apt update && sudo apt install -y ffmpeg
else
  echo "  ffmpeg OK: $(ffmpeg -version 2>&1 | head -1)"
fi

echo ""
echo "==> Verifying libass (required for subtitle burning)..."
if ! dpkg -l | grep -q libass; then
  sudo apt install -y libass-dev
fi
echo "  libass OK"

echo ""
echo "==> Verifying yt-dlp..."
yt-dlp --version

echo ""
echo "==> Verifying Whisper..."
python3 -c "import whisper; print('whisper OK, models dir:', whisper.__file__)"

echo ""
echo "==> All dependencies installed successfully."
echo ""
echo "IMPORTANT: Keep yt-dlp updated weekly for YouTube compatibility:"
echo "  pip install -U yt-dlp --break-system-packages"
echo ""
echo "Whisper model sizes (auto-downloaded on first use):"
echo "  tiny   ~39 MB   fastest, lower accuracy"
echo "  base   ~74 MB   good balance (default)"
echo "  small  ~244 MB  significantly better Portuguese accuracy (recommended)"
echo "  medium ~769 MB  best accuracy, needs ~2 GB RAM"
