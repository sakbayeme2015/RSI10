#!/usr/bin/env bash
# build.sh — compile the C core and install dependencies for RSI Vault.
set -e

echo "==> RSI Vault build"

# 1. System deps (OpenSSL dev headers needed to compile the C core)
if ! pkg-config --exists openssl 2>/dev/null && [ ! -f /usr/include/openssl/evp.h ]; then
  echo "==> Installing OpenSSL dev headers (needs sudo)..."
  if command -v apt-get >/dev/null; then
    sudo apt-get update && sudo apt-get install -y libssl-dev gcc python3 python3-pip
  else
    echo "!! Install OpenSSL dev headers manually for your distro, then re-run."
    exit 1
  fi
fi

# 2. Compile the C cryptographic core
echo "==> Compiling crypto_core.c -> libcryptocore.so"
gcc -O3 -shared -fPIC -o libcryptocore.so crypto_core.c -lcrypto
echo "   ok"

# 3. Python dependencies
echo "==> Installing Python dependencies (cryptography + conversion libs)"
pip3 install --break-system-packages \
  cryptography \
  pdf2docx pdfplumber openpyxl PyMuPDF Pillow numpy python-docx exifread SimpleITK pydicom pytesseract rawpy imageio \
  >/dev/null 2>&1 || pip3 install --user \
  cryptography pdf2docx pdfplumber openpyxl PyMuPDF Pillow numpy python-docx exifread SimpleITK pydicom pytesseract rawpy imageio
echo "   ok"

# 3.5 LibreOffice (only needed for Word/Excel -> PDF conversions)
if ! command -v soffice >/dev/null 2>&1; then
  echo "==> LibreOffice not found. It's needed ONLY for Word/Excel -> PDF."
  echo "    Install with:  sudo apt install libreoffice-writer libreoffice-calc ffmpeg tesseract-ocr"
  echo "    (skip if you don't need those two conversions)"
fi

# 4. Node dependencies
echo "==> Installing Node dependencies"
npm install
echo "   ok"

echo ""
echo "==> Build complete. Start the server with:"
echo "      node server.js"
echo "    then open http://localhost:3000"
