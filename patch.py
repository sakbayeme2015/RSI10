#!/usr/bin/env python3
"""
patch.py — RSI Vault SEO patcher
================================

Injects dynamic SEO routes into server.js:

    GET /robots.txt          -> plain-text robots
    GET /sitemap.xml         -> XML sitemap
    GET /site.webmanifest    -> PWA web app manifest
    GET /og-image.png        -> the PNG if it exists, else an on-the-fly SVG card

The routes are inserted BEFORE the express.static middleware so they take
precedence over any static files of the same name.

Behaviour:
  * Idempotent — running twice does nothing the second time (marker check).
  * Backs up server.js to server.js.bak before the first patch.
  * Best-effort generates public/og-image.png (1200x630) if Pillow is present.

Config:
  * The routes read SITE_URL from the environment at runtime, defaulting to
    https://your-domain.example — set SITE_URL when you launch the server:
        SITE_URL=https://mydomain.com node server.js

Usage:
    python3 patch.py            # apply
    python3 patch.py --revert   # restore server.js.bak
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "server.js")
BACKUP = SERVER + ".bak"
OG_PNG = os.path.join(ROOT, "public", "og-image.png")

MARKER = "SEO ROUTES — auto-added by patcher"

# The block injected into server.js. Raw string so JS backticks / ${} survive.
SEO_BLOCK = r"""// ===== SEO ROUTES — auto-added by patcher =====
const SITE_URL = process.env.SITE_URL || 'https://your-domain.example';

// robots.txt
app.get('/robots.txt', (req, res) => {
  res.type('text/plain').send(
    `# served dynamically by RSI Vault
User-agent: *
Allow: /
Sitemap: ${SITE_URL}/sitemap.xml
`);
});

// sitemap.xml
app.get('/sitemap.xml', (req, res) => {
  res.type('application/xml').send(
    `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>${SITE_URL}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>
</urlset>
`);
});

// site.webmanifest
app.get('/site.webmanifest', (req, res) => {
  res.type('application/manifest+json').send(JSON.stringify({
    name: 'RSI Vault',
    short_name: 'RSI Vault',
    description: 'Encrypt, convert and transform files locally in your browser.',
    start_url: '/',
    display: 'standalone',
    background_color: '#0c1a1d',
    theme_color: '#e0945a',
    icons: [{ src: '/og-image.png', sizes: '512x512', type: 'image/png' }]
  }, null, 2));
});

// og-image.png (auto SVG fallback if PNG not yet created)
app.get('/og-image.png', (req, res) => {
  const png = require('path').join(__dirname, 'public', 'og-image.png');
  const fs = require('fs');
  fs.access(png, fs.constants.R_OK, (err) => {
    if (!err) return res.type('image/png').sendFile(png);
    res.type('image/svg+xml').send(
      `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630">
        <rect width="1200" height="630" fill="#0c1a1d"/>
        <rect width="1200" height="10" fill="#e0945a"/>
        <text x="80" y="300" fill="#e0945a" font-family="Segoe UI, Arial, sans-serif" font-size="88" font-weight="700">RSI Vault</text>
        <text x="82" y="372" fill="#e7f0ec" font-family="Segoe UI, Arial, sans-serif" font-size="38">Encrypt · Convert · Transform — locally</text>
      </svg>`);
  });
});
// ===== END SEO ROUTES =====

"""


def revert():
    if not os.path.exists(BACKUP):
        print("! no server.js.bak to revert to")
        sys.exit(1)
    shutil.copy2(BACKUP, SERVER)
    print("• reverted server.js from server.js.bak")


def patch_server():
    with open(SERVER, encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print("• server.js already patched — skipping route injection")
        return False

    # Insert before the static middleware so routes win over static files.
    anchors = ['app.use(express.static(', '// Multer / size-limit error handler.']
    idx = -1
    for a in anchors:
        idx = src.find(a)
        if idx != -1:
            break
    if idx == -1:
        print("! could not find an insertion point in server.js")
        sys.exit(1)

    line_start = src.rfind("\n", 0, idx) + 1
    shutil.copy2(SERVER, BACKUP)
    new_src = src[:line_start] + SEO_BLOCK + src[line_start:]
    with open(SERVER, "w", encoding="utf-8") as f:
        f.write(new_src)

    print(f"• backed up original  -> {os.path.basename(BACKUP)}")
    print("• injected 4 SEO routes (robots, sitemap, manifest, og-image)")
    return True


def make_og_image():
    if os.path.exists(OG_PNG):
        print("• og-image.png already exists — leaving it in place")
        return
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("• Pillow not installed — skipping PNG (route serves SVG fallback)")
        return

    img = Image.new("RGB", (1200, 630), (12, 26, 29))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1200, 12], fill=(224, 148, 90))

    def font(size):
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        return ImageFont.load_default()

    d.text((80, 240), "RSI Vault", fill=(224, 148, 90), font=font(92))
    d.text((82, 360), "Encrypt · Convert · Transform — locally",
           fill=(231, 240, 236), font=font(40))
    os.makedirs(os.path.dirname(OG_PNG), exist_ok=True)
    img.save(OG_PNG)
    print("• generated default public/og-image.png (1200x630)")


def main():
    if "--revert" in sys.argv:
        revert()
        return
    print("RSI Vault SEO patcher")
    print("---------------------")
    changed = patch_server()
    make_og_image()
    print("done." if changed else "done (no server changes were needed).")


if __name__ == "__main__":
    main()
