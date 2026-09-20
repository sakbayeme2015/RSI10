// server.js — RSI Vault backend
// ---------------------------------------------------------------------------
// Express server that:
//   * serves the frontend (public/) at http://localhost:3000
//   * accepts file uploads (any type) via multer
//   * shells out to ratchet.py for the actual crypto (which calls the C core)
//   * streams the resulting encrypted/decrypted file back for download
//   * exposes the live ratchet state for the UI
//   * POST /api/youtube/translate — download + transcribe + translate YouTube videos
//
// The backend never sees the passphrase persisted — it is passed straight
// through to the Python worker as an argument and never written to disk.
// ---------------------------------------------------------------------------

const express = require("express");
const multer = require("multer");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");
const os = require("os");

// Load .env (simple, dependency-free) if present. Real environment variables
// already set on the process always win over the file.
(() => {
  try {
    const envPath = path.join(__dirname, ".env");
    if (!fs.existsSync(envPath)) return;
    for (const line of fs.readFileSync(envPath, "utf8").split("\n")) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (!m) continue;                       // skips blanks and # comments
      const key = m[1];
      let val = m[2].trim().replace(/^["']|["']$/g, ""); // strip quotes
      if (!(key in process.env)) process.env[key] = val;
    }
  } catch { /* ignore a malformed .env */ }
})();

const app = express();
const PORT = process.env.PORT || 3000;

const ROOT = __dirname;
const WORK_DIR = path.join(os.tmpdir(), "rsi-vault-work");
const RATCHET = path.join(ROOT, "ratchet.py");
const CONVERT = path.join(ROOT, "convert.py");

fs.mkdirSync(WORK_DIR, { recursive: true });

// Conversion catalogue. Each entry maps a UI "type" to a convert.py command,
// the output file extension, and a filename suffix.
//   accept: which input the converter expects ("pdf" | "image")
const CONVERSIONS = {
  pdf2docx:     { cmd: "pdf2docx",     extra: [],            ext: "docx", suffix: "",         accept: "pdf"   },
  pdf2xlsx:     { cmd: "pdf2xlsx",     extra: [],            ext: "xlsx", suffix: "",         accept: "pdf"   },
  pdf2img_png:  { cmd: "pdf2img",      extra: ["png", "150"],ext: "zip",  suffix: "_pages",   accept: "pdf"   },
  pdf2img_jpg:  { cmd: "pdf2img",      extra: ["jpg", "150"],ext: "zip",  suffix: "_pages",   accept: "pdf"   },
  img2obj:      { cmd: "img2relief",   extra: ["obj"],       ext: "zip",  suffix: "_3d_obj",  accept: "image" },
  img2stl:      { cmd: "img2relief",   extra: ["stl"],       ext: "zip",  suffix: "_3d_stl",  accept: "image" },
  img2anaglyph: { cmd: "img2anaglyph", extra: [],            ext: "png",  suffix: "_anaglyph",accept: "image" },
  docx2pdf:     { cmd: "office2pdf",   extra: [],            ext: "pdf",  suffix: "",          accept: "docx"  },
  xlsx2pdf:     { cmd: "office2pdf",   extra: [],            ext: "pdf",  suffix: "",          accept: "xlsx"  },
  img2pdf:      { cmd: "img2pdf",      extra: [],            ext: "pdf",  suffix: "",          accept: "image" },
  img_thermal:  { cmd: "img_thermal",  extra: [],            ext: "png",  suffix: "_thermal", accept: "image" },
  img_xray:     { cmd: "img_xray",     extra: [],            ext: "png",  suffix: "_xray",    accept: "image" },
  vid_thermal:  { cmd: "vid_thermal",  extra: [],            ext: "mp4",  suffix: "_thermal", accept: "video" },
  vid_3d:       { cmd: "vid_anaglyph", extra: [],            ext: "mp4",  suffix: "_3d",       accept: "video" },
  vid2mp3:      { cmd: "video2mp3",    extra: ["enhanced"],  ext: "mp3",  suffix: "",          accept: "video" },
  vid2mp3_max:  { cmd: "video2mp3",    extra: ["max"],       ext: "mp3",  suffix: "_MAX",       accept: "video" },
  vid2mp3_std:  { cmd: "video2mp3",    extra: ["standard"],  ext: "mp3",  suffix: "_standard",  accept: "video" },
  vid2ytmp4:    { cmd: "video2ytmp4",  extra: [],            ext: "mp4",  suffix: "_youtube",  accept: "video" },
  vid2webm:     { cmd: "video2webm",   extra: [],            ext: "webm", suffix: "",          accept: "video" },
  img2jpg:      { cmd: "img2img",      extra: ["jpg"],       ext: "jpg",  suffix: "",          accept: "image" },
  img2png:      { cmd: "img2img",      extra: ["png"],       ext: "png",  suffix: "",          accept: "image" },
  img2docx:     { cmd: "img2docx",     extra: [],            ext: "docx", suffix: "",          accept: "image" },
  med2png:      { cmd: "medconvert",   extra: ["png"],       ext: "png",  suffix: "_preview",  accept: "medical" },
  med2nii:      { cmd: "medconvert",   extra: ["nii"],       ext: "nii",  suffix: "",          accept: "medical" },
  med2nrrd:     { cmd: "medconvert",   extra: ["nrrd"],      ext: "nrrd", suffix: "",          accept: "medical" },
  med2mha:      { cmd: "medconvert",   extra: ["mha"],       ext: "mha",  suffix: "",          accept: "medical" },
  med2dcm:      { cmd: "medconvert",   extra: ["dcm"],       ext: "dcm",  suffix: "",          accept: "medical" },
  med2hdr:      { cmd: "medconvert",   extra: ["hdr"],       ext: "zip",  suffix: "_analyze",  accept: "medical" },
  med2mhd:      { cmd: "medconvert",   extra: ["mhd"],       ext: "zip",  suffix: "_mhd",      accept: "medical" },
  dcm_anon:     { cmd: "dcmanon",      extra: [],            ext: "dcm",  suffix: "_anonymized",accept: "dicom" },
  raw2jpg:      { cmd: "rawconvert",   extra: ["jpg"],       ext: "jpg",  suffix: "",          accept: "raw"   },
  raw2png:      { cmd: "rawconvert",   extra: ["png"],       ext: "png",  suffix: "",          accept: "raw"   },
  raw2tiff:     { cmd: "rawconvert",   extra: ["tiff"],      ext: "tiff", suffix: "",          accept: "raw"   },
  // ── iLovePDF-style PDF toolbox (single-file, no extra params) ──
  pdf2pptx:     { cmd: "pdf2pptx",     extra: [],            ext: "pptx", suffix: "",          accept: "pdf"   },
  pptx2pdf:     { cmd: "pptx2pdf",     extra: [],            ext: "pdf",  suffix: "",          accept: "pptx"  },
  pdf2html:     { cmd: "pdf2html",     extra: [],            ext: "html", suffix: "",          accept: "pdf"   },
  html2pdf:     { cmd: "html2pdf",     extra: [],            ext: "pdf",  suffix: "",          accept: "html"  },
  pdf2pdfa:     { cmd: "pdf2pdfa",     extra: [],            ext: "pdf",  suffix: "_pdfa",     accept: "pdf"   },
  pdf_compress: { cmd: "pdf_compress", extra: ["ebook"],     ext: "pdf",  suffix: "_compressed",accept: "pdf" },
  pdf_repair:   { cmd: "pdf_repair",   extra: [],            ext: "pdf",  suffix: "_repaired", accept: "pdf"   },
};

// Max upload size: 50 MB for crypto (whole-file AEAD happens in memory).
const MAX_BYTES = 50 * 1024 * 1024;
// Large uploads (video conversion): up to 3 GB, streamed to disk by multer.
const MAX_BYTES_LARGE = 3 * 1024 * 1024 * 1024;

const upload = multer({
  dest: WORK_DIR,
  limits: { fileSize: MAX_BYTES },
});

const uploadLarge = multer({
  dest: WORK_DIR,
  limits: { fileSize: MAX_BYTES_LARGE },
});

// ===== SEO ROUTES — auto-added by patcher =====
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

app.use(express.static(path.join(ROOT, "public")));
app.use(express.json());

// ---------------------------------------------------------------------------
// Helper: run ratchet.py with args, resolve with parsed JSON from stdout.
// ---------------------------------------------------------------------------
function runWorker(script, args) {
  return new Promise((resolve, reject) => {
    const py = spawn("python3", [script, ...args]);
    let stdout = "";
    let stderr = "";

    py.stdout.on("data", (c) => (stdout += c));
    py.stderr.on("data", (c) => (stderr += c));

    py.on("error", (err) =>
      reject(new Error(`failed to launch worker: ${err.message}`))
    );

    py.on("close", () => {
      const trimmed = stdout.trim();
      if (!trimmed) {
        return reject(new Error(stderr.trim() || "worker produced no output"));
      }
      try {
        resolve(JSON.parse(trimmed));
      } catch (e) {
        reject(new Error(`bad worker output: ${trimmed.slice(0, 200)}`));
      }
    });
  });
}

// Backwards-compatible helper for the crypto routes.
function runRatchet(args) {
  return runWorker(RATCHET, args);
}

// Safely remove a temp file, ignoring errors.
function cleanup(...paths) {
  for (const p of paths) {
    if (p) fs.promises.unlink(p).catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// POST /api/encrypt   (multipart: file, passphrase)
// ---------------------------------------------------------------------------
app.post("/api/encrypt", upload.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const passphrase = req.body.passphrase;
  const originalName = req.file && req.file.originalname;

  if (!inPath || !passphrase) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: "file and passphrase required" });
  }

  const token = crypto.randomBytes(6).toString("hex");
  const outPath = path.join(WORK_DIR, `enc_${token}.rsienc`);
  const namedIn = path.join(WORK_DIR, `${token}_in`);

  try {
    await fs.promises.rename(inPath, namedIn);
    const cleanName = sanitize(originalName);
    const result = await runRatchet(["encrypt", namedIn, outPath, passphrase, cleanName]);

    if (!result.ok) {
      cleanup(namedIn, outPath);
      return res.status(500).json(result);
    }

    res.setHeader("X-RSI-Meta", Buffer.from(JSON.stringify(result)).toString("base64"));
    res.setHeader("Content-Type", "application/octet-stream");
    res.setHeader(
      "Content-Disposition",
      `attachment; filename="${sanitize(originalName)}.rsienc"`
    );

    const stream = fs.createReadStream(outPath);
    stream.pipe(res);
    stream.on("close", () => cleanup(namedIn, outPath));
    stream.on("error", () => {
      cleanup(namedIn, outPath);
      if (!res.headersSent) res.status(500).end();
    });
  } catch (err) {
    cleanup(namedIn, outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/decrypt   (multipart: file (.rsienc), passphrase)
// ---------------------------------------------------------------------------
app.post("/api/decrypt", upload.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const passphrase = req.body.passphrase;

  if (!inPath || !passphrase) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: "file and passphrase required" });
  }

  const token = crypto.randomBytes(6).toString("hex");
  const outPath = path.join(WORK_DIR, `dec_${token}`);

  try {
    const result = await runRatchet(["decrypt", inPath, outPath, passphrase]);

    if (!result.ok) {
      cleanup(inPath, outPath);
      const status = String(result.error).includes("AUTH_FAIL") ? 422 : 500;
      return res.status(status).json(result);
    }

    res.setHeader("X-RSI-Meta", Buffer.from(JSON.stringify(result)).toString("base64"));
    res.setHeader("Content-Type", "application/octet-stream");
    res.setHeader(
      "Content-Disposition",
      `attachment; filename="${sanitize(result.original_name || "decrypted.bin")}"`
    );

    const stream = fs.createReadStream(outPath);
    stream.pipe(res);
    stream.on("close", () => cleanup(inPath, outPath));
    stream.on("error", () => {
      cleanup(inPath, outPath);
      if (!res.headersSent) res.status(500).end();
    });
  } catch (err) {
    cleanup(inPath, outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// GET /api/state — current ratchet position
// ---------------------------------------------------------------------------
app.get("/api/state", async (_req, res) => {
  try {
    res.json(await runRatchet(["state"]));
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/rekey — force a DH ratchet step
// ---------------------------------------------------------------------------
app.post("/api/rekey", async (_req, res) => {
  try {
    res.json(await runRatchet(["rekey"]));
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// GET /api/conversions — list available conversion types
// ---------------------------------------------------------------------------
app.get("/api/conversions", (_req, res) => {
  res.json({
    ok: true,
    conversions: Object.entries(CONVERSIONS).map(([id, c]) => ({
      id, ext: c.ext, accept: c.accept,
    })),
  });
});

// ---------------------------------------------------------------------------
// POST /api/convert   (multipart: file, type)
// ---------------------------------------------------------------------------
app.post("/api/convert", uploadLarge.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const type = req.body.type;
  const originalName = req.file && req.file.originalname;

  if (!inPath || !type) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: "file and conversion type required" });
  }
  const conv = CONVERSIONS[type];
  if (!conv) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: `unknown conversion type: ${type}` });
  }

  const token = crypto.randomBytes(6).toString("hex");
  const inExt = path.extname(originalName || "").toLowerCase();
  const namedIn = path.join(WORK_DIR, `${token}_in${inExt}`);
  const outPath = path.join(WORK_DIR, `conv_${token}.${conv.ext}`);

  const base = sanitize(originalName).replace(/\.[^.]+$/, "");
  const downloadName = `${base}${conv.suffix}.${conv.ext}`;

  const lower = String(originalName || "").toLowerCase();
  const isPdf   = lower.endsWith(".pdf");
  const isImg   = /\.(jpe?g|png|webp|bmp|gif|tiff?)$/.test(lower);
  const isDocx  = /\.docx?$/.test(lower);
  const isXlsx  = /\.xlsx?$/.test(lower);
  const isPptx  = /\.pptx?$/.test(lower);
  const isHtml  = /\.html?$/.test(lower);
  const isVideo = /\.(mp4|mkv|mov|avi|webm|m4v)$/.test(lower);
  const isMed   = /\.(dcm|dicom|nrrd|mha|mhd|hdr|img|nii|gz|jpe?g|png|bmp|tiff?|webp)$/.test(lower);
  const isDicom = /\.(dcm|dicom)$/.test(lower);
  const isRaw   = /\.(cr2|cr3|nef|arw|raf|orf|rw2|pef|x3f|3fr|rwl|dcr|mrw|dng|raw|srw|nrw|kdc)$/.test(lower);
  const guards = {
    pdf:     [isPdf,    "this conversion needs a PDF file"],
    image:   [isImg,    "this conversion needs an image (jpg/png/webp/…)"],
    docx:    [isDocx,   "this conversion needs a Word document (.docx)"],
    xlsx:    [isXlsx,   "this conversion needs an Excel spreadsheet (.xlsx)"],
    pptx:    [isPptx,   "this conversion needs a PowerPoint file (.pptx)"],
    html:    [isHtml,   "this conversion needs an HTML file (.html)"],
    video:   [isVideo,  "this conversion needs a video (.mp4/.mkv/.mov/…)"],
    medical: [isMed,    "this needs a medical image (.dcm/.nii/.nrrd/.mha/.mhd/.hdr) or a photo to wrap"],
    dicom:   [isDicom,  "the anonymizer needs a DICOM file (.dcm)"],
    raw:     [isRaw,    "this needs a RAW camera file (.cr2/.cr3/.nef/.arw/.dng/…)"],
  };
  const guard = guards[conv.accept];
  if (guard && !guard[0]) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: guard[1] });
  }

  try {
    await fs.promises.rename(inPath, namedIn);
    const workerArgs = [conv.cmd, namedIn, outPath, ...conv.extra];

    if (conv.cmd === "img2relief") {
      let depth = parseFloat(req.body.depth);
      if (!Number.isFinite(depth)) depth = 0.15;
      depth = Math.min(Math.max(depth, 0.02), 1.0);
      workerArgs.push(String(depth));
    }

    const result = await runWorker(CONVERT, workerArgs);

    if (!result.ok) {
      cleanup(namedIn, outPath);
      return res.status(422).json(result);
    }

    res.setHeader("X-RSI-Meta", Buffer.from(JSON.stringify(result)).toString("base64"));
    res.setHeader("Content-Type", "application/octet-stream");
    res.setHeader("Content-Disposition", `attachment; filename="${downloadName}"`);

    const stream = fs.createReadStream(outPath);
    stream.pipe(res);
    stream.on("close", () => cleanup(namedIn, outPath));
    stream.on("error", () => {
      cleanup(namedIn, outPath);
      if (!res.headersSent) res.status(500).end();
    });
  } catch (err) {
    cleanup(namedIn, outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// PDF TOOLBOX — endpoints that need extra parameters or multiple files.
// ---------------------------------------------------------------------------

function streamResult(res, result, outPath, downloadName, ...cleanupPaths) {
  if (!result.ok) {
    cleanup(...cleanupPaths, outPath);
    return res.status(422).json(result);
  }
  res.setHeader("X-RSI-Meta", Buffer.from(JSON.stringify(result)).toString("base64"));
  res.setHeader("Content-Type", "application/octet-stream");
  res.setHeader("Content-Disposition", `attachment; filename="${downloadName}"`);
  const stream = fs.createReadStream(outPath);
  stream.pipe(res);
  stream.on("close", () => cleanup(...cleanupPaths, outPath));
  stream.on("error", () => {
    cleanup(...cleanupPaths, outPath);
    if (!res.headersSent) res.status(500).end();
  });
}

// POST /api/pdf/merge
app.post("/api/pdf/merge", uploadLarge.array("files", 50), async (req, res) => {
  const files = req.files || [];
  if (files.length < 2) {
    files.forEach((f) => cleanup(f.path));
    return res.status(400).json({ ok: false, error: "upload at least 2 PDF files" });
  }
  const token = crypto.randomBytes(6).toString("hex");
  const named = [];
  const outPath = path.join(WORK_DIR, `merged_${token}.pdf`);
  try {
    for (let i = 0; i < files.length; i++) {
      const p = path.join(WORK_DIR, `${token}_${i}.pdf`);
      await fs.promises.rename(files[i].path, p);
      named.push(p);
    }
    const result = await runWorker(CONVERT, ["pdf_merge", named.join("|"), outPath]);
    streamResult(res, result, outPath, "merged.pdf", ...named);
  } catch (err) {
    cleanup(...named, outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

function pdfToolEndpoint(route, buildArgs, outExt, suffix) {
  app.post(route, uploadLarge.single("file"), async (req, res) => {
    const inPath = req.file && req.file.path;
    const originalName = req.file && req.file.originalname;
    if (!inPath) return res.status(400).json({ ok: false, error: "file required" });
    const lower = String(originalName || "").toLowerCase();
    if (!lower.endsWith(".pdf") && route !== "/api/pdf/from-html") {
      cleanup(inPath);
      return res.status(400).json({ ok: false, error: "this tool needs a PDF file" });
    }
    const token = crypto.randomBytes(6).toString("hex");
    const namedIn = path.join(WORK_DIR, `${token}_in.pdf`);
    const outPath = path.join(WORK_DIR, `${token}_out.${outExt}`);
    const base = sanitize(originalName).replace(/\.[^.]+$/, "");
    const dl = `${base}${suffix}.${outExt}`;
    try {
      await fs.promises.rename(inPath, namedIn);
      const args = buildArgs(namedIn, outPath, req.body || {});
      const result = await runWorker(CONVERT, args);
      streamResult(res, result, outPath, dl, namedIn);
    } catch (err) {
      cleanup(namedIn, outPath);
      res.status(500).json({ ok: false, error: err.message });
    }
  });
}

pdfToolEndpoint("/api/pdf/split",
  (i, o, b) => ["pdf_split", i, o, b.ranges || ""], "zip", "_split");

pdfToolEndpoint("/api/pdf/remove",
  (i, o, b) => ["pdf_remove", i, o, b.pages || ""], "pdf", "_removed");

pdfToolEndpoint("/api/pdf/extract",
  (i, o, b) => ["pdf_extract", i, o, b.pages || ""], "pdf", "_extracted");

pdfToolEndpoint("/api/pdf/rotate",
  (i, o, b) => ["pdf_rotate", i, o, String(b.degrees || 90), b.pages || ""],
  "pdf", "_rotated");

pdfToolEndpoint("/api/pdf/pagenum",
  (i, o, b) => ["pdf_pagenum", i, o, b.position || "bottom-center", String(b.start || 1)],
  "pdf", "_numbered");

pdfToolEndpoint("/api/pdf/watermark",
  (i, o, b) => ["pdf_watermark", i, o, b.text || "CONFIDENTIAL", String(b.opacity || 0.15)],
  "pdf", "_watermarked");

app.post("/api/pdf/protect", uploadLarge.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const originalName = req.file && req.file.originalname;
  const password = req.body && req.body.password;
  if (!inPath) return res.status(400).json({ ok: false, error: "file required" });
  if (!password) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: "a password is required" });
  }
  const token = crypto.randomBytes(6).toString("hex");
  const namedIn = path.join(WORK_DIR, `${token}_in.pdf`);
  const outPath = path.join(WORK_DIR, `${token}_out.pdf`);
  const base = sanitize(originalName).replace(/\.[^.]+$/, "");
  try {
    await fs.promises.rename(inPath, namedIn);
    const result = await runWorker(CONVERT, ["pdf_protect", namedIn, outPath, password]);
    streamResult(res, result, outPath, `${base}_protected.pdf`, namedIn);
  } catch (err) {
    cleanup(namedIn, outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post("/api/pdf/unlock", uploadLarge.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const originalName = req.file && req.file.originalname;
  const password = (req.body && req.body.password) || "";
  if (!inPath) return res.status(400).json({ ok: false, error: "file required" });
  const token = crypto.randomBytes(6).toString("hex");
  const namedIn = path.join(WORK_DIR, `${token}_in.pdf`);
  const outPath = path.join(WORK_DIR, `${token}_out.pdf`);
  const base = sanitize(originalName).replace(/\.[^.]+$/, "");
  try {
    await fs.promises.rename(inPath, namedIn);
    const result = await runWorker(CONVERT, ["pdf_unlock", namedIn, outPath, password]);
    if (!result.ok) {
      cleanup(namedIn, outPath);
      const status = String(result.error).includes("AUTH_FAIL") ? 422 : 500;
      return res.status(status).json(result);
    }
    streamResult(res, result, outPath, `${base}_unlocked.pdf`, namedIn);
  } catch (err) {
    cleanup(namedIn, outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/fixdocx
// ---------------------------------------------------------------------------
app.post("/api/fixdocx", uploadLarge.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const originalName = req.file && req.file.originalname;

  if (!inPath) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: "file required" });
  }

  const lower = String(originalName || "").toLowerCase();
  if (!/\.docx?$/.test(lower)) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: "this repair needs a Word document (.docx)" });
  }

  const token = crypto.randomBytes(6).toString("hex");
  const namedIn = path.join(WORK_DIR, `${token}_fix.docx`);
  const outPath = path.join(WORK_DIR, `fixed_${token}.docx`);
  const base = sanitize(originalName).replace(/\.[^.]+$/, "");
  const downloadName = `${base}_repaired.docx`;

  try {
    await fs.promises.rename(inPath, namedIn);
    const result = await runWorker(CONVERT, ["fixdocx", namedIn, outPath]);

    if (!result.ok) {
      cleanup(namedIn, outPath);
      return res.status(422).json(result);
    }
    if (!result.normalized) {
      cleanup(namedIn, outPath);
      return res.status(503).json({
        ok: false,
        error: "LibreOffice is required to repair .docx files. "
             + "Install it with:  sudo apt install libreoffice",
      });
    }

    res.setHeader("X-RSI-Meta", Buffer.from(JSON.stringify(result)).toString("base64"));
    res.setHeader("Content-Type", "application/octet-stream");
    res.setHeader("Content-Disposition", `attachment; filename="${downloadName}"`);

    const stream = fs.createReadStream(outPath);
    stream.pipe(res);
    stream.on("close", () => cleanup(namedIn, outPath));
    stream.on("error", () => {
      cleanup(namedIn, outPath);
      if (!res.headersSent) res.status(500).end();
    });
  } catch (err) {
    cleanup(namedIn, outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/preview  (image -> .obj for 3D preview)
// ---------------------------------------------------------------------------
app.post("/api/preview", upload.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const originalName = req.file && req.file.originalname;

  if (!inPath) {
    return res.status(400).json({ ok: false, error: "image required" });
  }
  const lower = String(originalName || "").toLowerCase();
  if (!/\.(jpe?g|png|webp|bmp|gif|tiff?)$/.test(lower)) {
    cleanup(inPath);
    return res.status(400).json({ ok: false, error: "preview needs an image (jpg/png/…)" });
  }

  let depth = parseFloat(req.body.depth);
  if (!Number.isFinite(depth)) depth = 0.15;
  depth = Math.min(Math.max(depth, 0.02), 1.0);

  const token = crypto.randomBytes(6).toString("hex");
  const namedIn = path.join(WORK_DIR, `${token}_pin`);
  const outObj = path.join(WORK_DIR, `prev_${token}.obj`);

  try {
    await fs.promises.rename(inPath, namedIn);
    const result = await runWorker(CONVERT, ["previewobj", namedIn, outObj, String(depth)]);
    if (!result.ok) {
      cleanup(namedIn, outObj);
      return res.status(422).json(result);
    }
    res.setHeader("X-RSI-Meta", Buffer.from(JSON.stringify(result)).toString("base64"));
    res.setHeader("Content-Type", "text/plain");
    const stream = fs.createReadStream(outObj);
    stream.pipe(res);
    stream.on("close", () => cleanup(namedIn, outObj));
    stream.on("error", () => {
      cleanup(namedIn, outObj);
      if (!res.headersSent) res.status(500).end();
    });
  } catch (err) {
    cleanup(namedIn, outObj);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/geolocate
// ---------------------------------------------------------------------------
app.post("/api/geolocate", uploadLarge.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const originalName = req.file && req.file.originalname;
  if (!inPath) {
    return res.status(400).json({ ok: false, error: "file required" });
  }

  const token = crypto.randomBytes(6).toString("hex");
  const inExt = path.extname(originalName || "").toLowerCase();
  const namedIn = path.join(WORK_DIR, `${token}_geo${inExt}`);

  try {
    await fs.promises.rename(inPath, namedIn);
    const result = await runWorker(CONVERT, ["geolocate", namedIn]);
    cleanup(namedIn);
    res.json(result);
  } catch (err) {
    cleanup(namedIn, inPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/scantext
// ---------------------------------------------------------------------------
app.post("/api/scantext", uploadLarge.single("file"), async (req, res) => {
  const inPath = req.file && req.file.path;
  const originalName = req.file && req.file.originalname;
  if (!inPath) return res.status(400).json({ ok: false, error: "file required" });

  const token = crypto.randomBytes(6).toString("hex");
  const inExt = path.extname(originalName || "").toLowerCase();
  const namedIn = path.join(WORK_DIR, `${token}_ocr${inExt}`);
  try {
    await fs.promises.rename(inPath, namedIn);
    const result = await runWorker(CONVERT, ["scantext", namedIn]);
    cleanup(namedIn);
    res.json(result);
  } catch (err) {
    cleanup(namedIn, inPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/youtube/translate
//
// Downloads a YouTube video, transcribes (Whisper), translates (Google
// Translate free API via deep-translator), then returns either:
//   - A translated MP4 with burned subtitles  (mode="subtitle", default)
//   - A translated MP4 with dubbed TTS audio  (mode="dub")
//   - A translated .srt subtitle file only    (mode="srt", fastest)
//
// Required server packages:
//   pip install yt-dlp openai-whisper deep-translator edge-tts
//   sudo apt install ffmpeg   (already required)
//
// Body fields (JSON):
//   url       {string}  Required. YouTube video URL.
//   src_lang  {string}  Source language ISO code.   Default: "pt"
//                       "pt" covers both Brazilian AND Portugal Portuguese.
//   tgt_lang  {string}  Target language ISO code.   Default: "fr" (French)
//   mode      {string}  "subtitle" | "srt" | "dub". Default: "subtitle"
//   model     {string}  Whisper model size.         Default: "base"
//                       Use "small" for significantly better Portuguese accuracy.
//
// Example curl:
//   curl -X POST http://localhost:3000/api/youtube/translate \
//        -H "Content-Type: application/json" \
//        -d '{"url":"https://www.youtube.com/watch?v=XXXX","src_lang":"pt","tgt_lang":"fr","mode":"subtitle"}' \
//        -o translated_fr.mp4
// ---------------------------------------------------------------------------
app.post("/api/youtube/translate", async (req, res) => {
  const body = req.body || {};
  const {
    url,
    src_lang = "pt",
    tgt_lang = "fr",
    mode     = "subtitle",
    model    = "base",
  } = body;

  // Validate
  if (!url || typeof url !== "string" || !url.trim()) {
    return res.status(400).json({
      ok: false,
      error: "Missing required field: url (must be a YouTube link).",
    });
  }
  const isYouTube = /youtube\.com\/watch|youtu\.be\/|youtube\.com\/shorts/.test(url);
  if (!isYouTube) {
    return res.status(400).json({
      ok: false,
      error: "url must be a YouTube link (youtube.com/watch?v=… or youtu.be/…).",
    });
  }
  const VALID_MODES = ["subtitle", "srt", "dub", "both"];
  if (!VALID_MODES.includes(mode)) {
    return res.status(400).json({
      ok: false,
      error: `Invalid mode "${mode}". Choose one of: ${VALID_MODES.join(", ")}.`,
    });
  }
  const VALID_MODELS = ["tiny", "base", "small", "medium", "large"];
  if (!VALID_MODELS.includes(model)) {
    return res.status(400).json({
      ok: false,
      error: `Invalid model "${model}". Choose one of: ${VALID_MODELS.join(", ")}.`,
    });
  }

  const token   = crypto.randomBytes(6).toString("hex");
  const outExt  = mode === "srt" ? "srt" : "mp4";
  const outPath = path.join(WORK_DIR, `yttrans_${token}.${outExt}`);
  const dlName  = `youtube_${tgt_lang}_${mode}.${outExt}`;

  // Note: server.timeout = 0 is set below, so long downloads/transcriptions
  // won't be cut off. A 10-min video typically takes 3–8 min on VPS CPU.
  try {
    const result = await runWorker(CONVERT, [
      "youtube_translate",
      url,
      outPath,
      src_lang,
      tgt_lang,
      mode,
      model,
    ]);

    if (!result.ok) {
      cleanup(outPath);
      return res.status(422).json(result);
    }

    const mimeType = outExt === "srt" ? "text/plain" : "application/octet-stream";
    res.setHeader("X-RSI-Meta", Buffer.from(JSON.stringify(result)).toString("base64"));
    res.setHeader("Content-Type", mimeType);
    res.setHeader("Content-Disposition", `attachment; filename="${dlName}"`);

    const stream = fs.createReadStream(outPath);
    stream.pipe(res);
    stream.on("close", () => cleanup(outPath));
    stream.on("error", () => {
      cleanup(outPath);
      if (!res.headersSent) res.status(500).end();
    });
  } catch (err) {
    cleanup(outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// POST /api/video/translate  (multipart: file + form fields)
//
// Translates a LOCAL video file already on the user's computer.
// Same pipeline as /api/youtube/translate but skips the yt-dlp download —
// the video is uploaded directly as a multipart/form-data file.
//
// Form fields (sent alongside the file):
//   src_lang  {string}  Source language ISO code.  Default: "en"
//   tgt_lang  {string}  Target language ISO code.  Default: "fr"
//   mode      {string}  "subtitle" | "srt" | "dub". Default: "subtitle"
//   model     {string}  Whisper model size.         Default: "base"
//
// Accepts: mp4, mkv, mov, avi, webm, m4v, flv, wmv, ts — up to 3 GB.
// Returns: translated mp4 (subtitle/dub) or srt file.
// ---------------------------------------------------------------------------
app.post("/api/video/translate", uploadLarge.single("file"), async (req, res) => {
  const inPath      = req.file && req.file.path;
  const originalName = req.file && req.file.originalname;

  if (!inPath) {
    return res.status(400).json({ ok: false, error: "video file required" });
  }

  const lower = String(originalName || "").toLowerCase();
  if (!/\.(mp4|mkv|mov|avi|webm|m4v|flv|wmv|ts|mts|mpeg|mpg)$/.test(lower)) {
    cleanup(inPath);
    return res.status(400).json({
      ok: false,
      error: "file must be a video (.mp4 · .mkv · .mov · .avi · .webm · …)",
    });
  }

  const {
    src_lang = "en",
    tgt_lang = "fr",
    mode     = "subtitle",
    model    = "base",
  } = req.body || {};

  const VALID_MODES  = ["subtitle", "srt", "dub", "both"];
  const VALID_MODELS = ["tiny", "base", "small", "medium", "large"];

  if (!VALID_MODES.includes(mode)) {
    cleanup(inPath);
    return res.status(400).json({
      ok: false,
      error: `Invalid mode "${mode}". Choose: ${VALID_MODES.join(", ")}.`,
    });
  }
  if (!VALID_MODELS.includes(model)) {
    cleanup(inPath);
    return res.status(400).json({
      ok: false,
      error: `Invalid model "${model}". Choose: ${VALID_MODELS.join(", ")}.`,
    });
  }

  const token    = crypto.randomBytes(6).toString("hex");
  const inExt    = path.extname(originalName || ".mp4").toLowerCase();
  const namedIn  = path.join(WORK_DIR, `${token}_lvt${inExt}`);
  const outExt   = mode === "srt" ? "srt" : "mp4";
  const outPath  = path.join(WORK_DIR, `lvtrans_${token}.${outExt}`);
  const base     = sanitize(originalName).replace(/\.[^.]+$/, "");
  const dlName   = `${base}_${tgt_lang}_${mode}.${outExt}`;

  try {
    await fs.promises.rename(inPath, namedIn);

    const result = await runWorker(CONVERT, [
      "local_video_translate",
      namedIn,
      outPath,
      src_lang,
      tgt_lang,
      mode,
      model,
    ]);

    if (!result.ok) {
      cleanup(namedIn, outPath);
      return res.status(422).json(result);
    }

    const mimeType = outExt === "srt" ? "text/plain" : "application/octet-stream";
    res.setHeader("X-RSI-Meta", Buffer.from(JSON.stringify(result)).toString("base64"));
    res.setHeader("Content-Type", mimeType);
    res.setHeader("Content-Disposition", `attachment; filename="${dlName}"`);

    const stream = fs.createReadStream(outPath);
    stream.pipe(res);
    stream.on("close", () => cleanup(namedIn, outPath));
    stream.on("error", () => {
      cleanup(namedIn, outPath);
      if (!res.headersSent) res.status(500).end();
    });
  } catch (err) {
    cleanup(namedIn, outPath);
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ---------------------------------------------------------------------------
// Multer / size-limit error handler.
// ---------------------------------------------------------------------------
app.use((err, _req, res, _next) => {
  if (err && err.code === "LIMIT_FILE_SIZE") {
    return res.status(413).json({ ok: false, error: "file too large (max 3 GB)" });
  }
  res.status(500).json({ ok: false, error: err.message || "server error" });
});

function sanitize(name) {
  return String(name || "file").replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 120);
}

const server = app.listen(PORT, () => {
  console.log("");
  console.log("  ╔══════════════════════════════════════════════════════╗");
  console.log("  ║   RSI Vault — ratcheting envelope file encryption     ║");
  console.log(`  ║   Frontend : http://localhost:${PORT}                     ║`);
  console.log("  ║   Crypto   : C (OpenSSL) + Python ratchet + Node API  ║");
  console.log("  ║   YouTube  : /api/youtube/translate  (pt→fr + more)   ║");
  console.log("  ╚══════════════════════════════════════════════════════╝");
  console.log("");
});

// Large uploads + long video encodes + YouTube processing need generous timeouts.
server.requestTimeout = 0;    // no cap on how long a request may take
server.headersTimeout = 0;
server.timeout = 0; 


