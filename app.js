// app.js — RSI Vault frontend logic
// Talks to the Node backend, drives the operation panel and the live
// ratchet-state visualization.

const els = {
  dropzone:         document.getElementById("dropzone"),
  fileInput:        document.getElementById("file-input"),
  dzTitle:          document.getElementById("dz-title"),
  dzSub:            document.getElementById("dz-sub"),
  dzInner:          document.getElementById("dz-inner"),
  passphrase:       document.getElementById("passphrase"),
  togglePass:       document.getElementById("toggle-pass"),
  passHint:         document.getElementById("pass-hint"),
  runBtn:           document.getElementById("run-btn"),
  runLabel:         document.getElementById("run-label"),
  status:           document.getElementById("status"),
  // tabs
  tabEncrypt:       document.getElementById("tab-encrypt"),
  tabDecrypt:       document.getElementById("tab-decrypt"),
  tabConvert:       document.getElementById("tab-convert"),
  tabEffects:       document.getElementById("tab-effects"),
  tabVideo:         document.getElementById("tab-video"),
  tabStudio:        document.getElementById("tab-studio"),
  tabGeo:           document.getElementById("tab-geo"),
  tabYoutube:       document.getElementById("tab-youtube"),
  // field panels
  convertField:     document.getElementById("convert-field"),
  effectsField:     document.getElementById("effects-field"),
  videoField:       document.getElementById("video-field"),
  studioField:      document.getElementById("studio-field"),
  geoField:         document.getElementById("geo-field"),
  youtubeField:     document.getElementById("youtube-field"),
  // geo
  geoStatus:        document.getElementById("geo-status"),
  geoCoords:        document.getElementById("geo-coords"),
  geoLat:           document.getElementById("geo-lat"),
  geoLon:           document.getElementById("geo-lon"),
  geoGmaps:         document.getElementById("geo-gmaps"),
  geoMap:           document.getElementById("geo-map"),
  // effects
  effectsType:      document.getElementById("effects-type"),
  videoType:        document.getElementById("video-type"),
  fxPreviewBtn:     document.getElementById("fx-preview-btn"),
  fxPreviewStage:   document.getElementById("fx-preview-stage"),
  fxPreview:        document.getElementById("fx-preview"),
  fxPreviewCap:     document.getElementById("fx-preview-cap"),
  // studio
  studioText:       document.getElementById("studio-text"),
  fontSize:         document.getElementById("font-size"),
  fsVal:            document.getElementById("fs-val"),
  textColor:        document.getElementById("text-color"),
  strokeColor:      document.getElementById("stroke-color"),
  bandToggle:       document.getElementById("band-toggle"),
  textAlign:        document.getElementById("text-align"),
  textPos:          document.getElementById("text-pos"),
  studioStage:      document.getElementById("studio-stage"),
  studioCanvas:     document.getElementById("studio-canvas"),
  studioActions:    document.getElementById("studio-actions"),
  studioHint:       document.getElementById("studio-hint"),
  studioPng:        document.getElementById("studio-png"),
  studioJpg:        document.getElementById("studio-jpg"),
  // convert
  passphraseField:  document.getElementById("passphrase-field"),
  convertType:      document.getElementById("convert-type"),
  convertHint:      document.getElementById("convert-hint"),
  depthControl:     document.getElementById("depth-control"),
  depthSlider:      document.getElementById("depth-slider"),
  depthValue:       document.getElementById("depth-value"),
  previewBtn:       document.getElementById("preview-btn"),
  spinBtn:          document.getElementById("spin-btn"),
  viewer:           document.getElementById("viewer"),
  viewerCanvas:     document.getElementById("viewer-canvas"),
  imgPreviewControl:document.getElementById("img-preview-control"),
  imgPreviewBtn:    document.getElementById("img-preview-btn"),
  imgPreviewStage:  document.getElementById("img-preview-stage"),
  imgPreview:       document.getElementById("img-preview"),
  imgPreviewCap:    document.getElementById("img-preview-cap"),
  // PDF tool parameter panels
  pdfParams:        document.getElementById("pdf-params"),
  paramMerge:       document.getElementById("param-merge"),
  mergeFiles:       document.getElementById("merge-files"),
  paramRanges:      document.getElementById("param-ranges"),
  pdfRanges:        document.getElementById("pdf-ranges"),
  paramPages:       document.getElementById("param-pages"),
  pdfPages:         document.getElementById("pdf-pages"),
  paramRotate:      document.getElementById("param-rotate"),
  pdfDegrees:       document.getElementById("pdf-degrees"),
  paramPagenum:     document.getElementById("param-pagenum"),
  pagenumPosition:  document.getElementById("pagenum-position"),
  paramWatermark:   document.getElementById("param-watermark"),
  watermarkText:    document.getElementById("watermark-text"),
  watermarkOpacity: document.getElementById("watermark-opacity"),
  watermarkOpacityVal: document.getElementById("watermark-opacity-val"),
  paramCompress:    document.getElementById("param-compress"),
  compressLevel:    document.getElementById("compress-level"),
  paramPassword:    document.getElementById("param-password"),
  pdfPassword:      document.getElementById("pdf-password"),
  pdfPasswordHint:  document.getElementById("pdf-password-hint"),
  // YouTube translation
  ytUrl:            document.getElementById("yt-url"),
  ytSrcLang:        document.getElementById("yt-src-lang"),
  ytTgtLang:        document.getElementById("yt-tgt-lang"),
  ytMode:           document.getElementById("yt-mode"),
  ytModel:          document.getElementById("yt-model"),
  ytModeHint:       document.getElementById("yt-mode-hint"),
  // YouTube source toggle
  ytSrcYoutube:     document.getElementById("yt-src-youtube"),
  ytSrcLocal:       document.getElementById("yt-src-local"),
  ytUrlSection:     document.getElementById("yt-url-section"),
  ytLocalSection:   document.getElementById("yt-local-section"),
  ytLocalDropzone:  document.getElementById("yt-local-dropzone"),
  ytLocalFile:      document.getElementById("yt-local-file"),
  ytLocalTitle:     document.getElementById("yt-local-title"),
  ytLocalSub:       document.getElementById("yt-local-sub"),
  // state panel
  epoch:            document.getElementById("m-epoch"),
  ops:              document.getElementById("m-ops"),
  dhpub:            document.getElementById("m-dhpub"),
  chain:            document.getElementById("chain"),
  chainCaption:     document.getElementById("chain-caption"),
  healCount:        document.getElementById("heal-count"),
  epochFill:        document.getElementById("epoch-fill"),
  rekeyBtn:         document.getElementById("rekey-btn"),
};

let mode = "encrypt";    // "encrypt" | "decrypt" | "convert" | "effects" | "video" | "studio" | "geo" | "youtube"
let selectedFile = null;
let rekeyEvery = 5;
let ytSource    = "youtube";   // "youtube" | "local" — which source is active in the YouTube tab
let ytLocalFile = null;        // the local video File object when ytSource === "local"

// ---------------------------------------------------------------------------
// Mode toggle
// ---------------------------------------------------------------------------
function setMode(next) {
  mode = next;
  const enc  = mode === "encrypt";
  const dec  = mode === "decrypt";
  const conv = mode === "convert";
  const eff  = mode === "effects";
  const vid  = mode === "video";
  const stu  = mode === "studio";
  const geo  = mode === "geo";
  const yt   = mode === "youtube";
  const isConvertLike = conv || eff || vid;

  // Tab active state
  [
    ["tabEncrypt", enc], ["tabDecrypt", dec], ["tabConvert", conv],
    ["tabEffects", eff], ["tabVideo",  vid],  ["tabStudio",  stu],
    ["tabGeo",     geo], ["tabYoutube", yt],
  ].forEach(([k, on]) => {
    if (els[k]) {
      els[k].classList.toggle("active", on);
      els[k].setAttribute("aria-selected", String(on));
    }
  });

  // In YouTube mode the dropzone is not needed — hide it.
  if (els.dropzone) els.dropzone.hidden = yt;

  // Field visibility
  els.passphraseField.hidden = isConvertLike || stu || geo || yt;
  els.convertField.hidden  = !conv;
  els.effectsField.hidden  = !eff;
  els.videoField.hidden    = !vid;
  els.studioField.hidden   = !stu;
  els.geoField.hidden      = !geo;
  els.youtubeField.hidden  = !yt;

  // Main run button: hidden for studio/geo (they have their own controls).
  els.runBtn.style.display = (stu || geo) ? "none" : "";

  els.runLabel.textContent =
    yt  ? "▶ Translate video" :
    eff ? "Apply effect" :
    vid ? "Process video" :
    conv ? "Convert file" :
    enc  ? "Encrypt file"  : "Decrypt file";

  if (enc) {
    els.dzSub.textContent = "Any type — pdf, jpg, xlsx, docx, zip, keys. Up to 50 MB.";
    els.passHint.textContent = "Argon2id derives a memory-hard root key from this. There is no recovery if you forget it.";
  } else if (dec) {
    els.dzSub.textContent = "Select a .rsienc container produced by this vault.";
    els.passHint.textContent = "Enter the same passphrase used to encrypt. A wrong one fails authentication — it cannot silently produce garbage.";
  } else if (conv) {
    updateConvertHint();
  } else if (eff) {
    els.dzSub.textContent = "Select an image (jpg/png) to restyle.";
  } else if (vid) {
    els.dzSub.textContent = "Select a video (.mp4/.mkv/.mov) to process.";
  } else if (stu) {
    els.dzSub.textContent = "Drop an image to use as the base for your thumbnail.";
  } else if (geo) {
    els.dzSub.textContent = "Drop a photo or video to read its GPS location.";
  }

  clearStatus();
  refreshRunState();
}

function updateConvertHint() {
  const type = els.convertType.value;
  const isRelief = type === "img2obj" || type === "img2stl";

  let needs;
  if (type.startsWith("pdf2"))      needs = "Select a PDF file to convert.";
  else if (type === "docx2pdf")     needs = "Select a Word document (.docx) to convert.";
  else if (type === "xlsx2pdf")     needs = "Select an Excel spreadsheet (.xlsx) to convert.";
  else if (type.startsWith("vid2")) needs = "Select a video (.mp4/.mkv/.mov/.avi/.webm) — large files up to 3 GB are supported.";
  else if (type.startsWith("med2")) needs = "Select a medical image (.dcm/.nii/.nrrd/.mha/.mhd/.hdr) — or a photo to wrap.";
  else if (type === "dcm_anon")     needs = "Select a DICOM file (.dcm) to strip patient identifiers.";
  else if (type.startsWith("raw2")) needs = "Select a RAW camera file (.cr2/.cr3/.nef/.arw/.raf/.dng/…).";
  else if (type === "scan_text")    needs = "Select an image, medical file, or RAW to scan its pixels for text.";
  else                              needs = "Select an image (jpg/png) to convert.";
  els.dzSub.textContent = needs;

  if (type.startsWith("pdf2")) {
    els.convertHint.textContent = "PDF → Word/Excel works best on text-based PDFs. Image export renders every page.";
  } else if (type === "dcm_anon") {
    els.convertHint.textContent = "Removes patient name, ID, dates, physician and institution tags. Note: it can't erase text burned into the image pixels — check visually before sharing.";
  } else if (type.startsWith("raw2")) {
    els.convertHint.textContent = "Decodes RAW with LibRaw (camera white balance). TIFF keeps 16-bit depth for editing; JPG/PNG are 8-bit.";
  } else if (type === "scan_text") {
    els.convertHint.textContent = "Runs OCR on the pixels and flags text that looks like a name, date, or ID — a check before sharing. Shows a report here (no file download).";
  } else if (type.startsWith("med2")) {
    els.convertHint.textContent = "Converts between DICOM/NIfTI/NRRD/MetaImage/Analyze, or renders a slice to PNG. Format conversion only — nothing is diagnosed.";
  } else if (type.endsWith("2pdf")) {
    els.convertHint.textContent = "Word/Excel → PDF uses LibreOffice for faithful layout. Image → PDF wraps the picture in a page.";
  } else if (type.startsWith("vid2mp3")) {
    if (type === "vid2mp3_max") {
      els.convertHint.textContent =
        "MAXIMUM loudness: -9 LUFS + heavy compression + +6dB boost. " +
        "Best for noisy environments. Audio is as loud as possible without clipping.";
    } else if (type === "vid2mp3_std") {
      els.convertHint.textContent =
        "Standard: 192k + loudnorm normalization. " +
        "Consistent level, smaller file size.";
    } else {
      els.convertHint.textContent =
        "Enhanced (recommended): 320k + loudnorm + dynamic compression + " +
        "+3dB presence boost. Clear, loud, punchy audio.";
    }
  } else if (type === "vid2ytmp4" || type === "vid2webm") {
    els.convertHint.textContent = "Full video re-encode. MP4 (H.264/AAC) for YouTube. WebM (VP9/Opus) for web. Large files take a while.";
  } else if (type === "img2docx") {
    els.convertHint.textContent = "Embeds your image in an editable Word document — open it in Word to crop, annotate, or resize.";
  } else if (type === "img2jpg" || type === "img2png") {
    els.convertHint.textContent = "Straight image format conversion.";
  } else {
    els.convertHint.textContent = "3D options turn image brightness into a relief mesh, or a red/cyan anaglyph.";
  }

  els.depthControl.hidden = !isRelief;

  const canImgPreview = type.startsWith("med2") || type === "dcm_anon" || type.startsWith("raw2");
  els.imgPreviewControl.hidden = !canImgPreview;
  if (!canImgPreview) els.imgPreviewStage.hidden = true;

  updatePdfParamPanels(type);
  refreshRunState();
}

const PDF_TOOL_PARAMS = {
  pdf_merge:     ["paramMerge"],
  pdf_split:     ["paramRanges"],
  pdf_remove:    ["paramPages"],
  pdf_extract:   ["paramPages"],
  pdf_rotate:    ["paramRotate"],
  pdf_pagenum:   ["paramPagenum"],
  pdf_watermark: ["paramWatermark"],
  pdf_compress:  ["paramCompress"],
  pdf_protect:   ["paramPassword"],
  pdf_unlock:    ["paramPassword"],
};

function updatePdfParamPanels(type) {
  const panels = ["paramMerge", "paramRanges", "paramPages", "paramRotate",
                  "paramPagenum", "paramWatermark", "paramCompress", "paramPassword"];
  const show = PDF_TOOL_PARAMS[type] || [];
  panels.forEach((p) => { if (els[p]) els[p].hidden = !show.includes(p); });
  if (els.pdfParams) els.pdfParams.hidden = show.length === 0;

  if (type === "pdf_protect" && els.pdfPasswordHint)
    els.pdfPasswordHint.textContent = "This password will be required to open the PDF (AES-256).";
  else if (type === "pdf_unlock" && els.pdfPasswordHint)
    els.pdfPasswordHint.textContent = "Enter the PDF's current password to remove protection.";

  const toolHints = {
    pdf_merge:     "Upload the FIRST PDF above, then add the rest here. They join in order.",
    pdf_split:     "Splits into separate PDFs (zipped). Leave ranges blank for one file per page.",
    pdf_remove:    "Deletes the listed pages, keeps the rest.",
    pdf_extract:   "Keeps only the listed pages.",
    pdf_rotate:    "Rotates every page (or a subset) by the chosen angle.",
    pdf_compress:  "Shrinks file size. Uses Ghostscript when available for best results.",
    pdf_repair:    "Re-parses a damaged PDF and rewrites it cleanly.",
    pdf_pagenum:   "Stamps page numbers on every page.",
    pdf_watermark: "Overlays diagonal watermark text on every page.",
    pdf_protect:   "Encrypts the PDF with a password (AES-256).",
    pdf_unlock:    "Removes password protection (you must know the password).",
    pdf2pptx:      "Each PDF page becomes a full slide — layout preserved exactly.",
    pdf2html:      "Exports a self-contained HTML with positioned text and embedded images.",
    pdf2pdfa:      "Converts to PDF/A for long-term archiving (needs Ghostscript).",
    pptx2pdf:      "PowerPoint → PDF via LibreOffice for faithful layout.",
    html2pdf:      "HTML → PDF (wkhtmltopdf if available, else LibreOffice).",
    pdf2docx_ocr:  "Forces OCR even if a text layer exists — good for garbled scans.",
  };
  if (toolHints[type]) els.convertHint.textContent = toolHints[type];
  if (type === "pdf2docx") {
    els.convertHint.textContent =
      "Text PDFs keep their layout. Scanned/image-only PDFs are auto-detected and OCR'd so the Word file has editable text.";
  }
}

// Update the mode hint text when YouTube mode selector changes.
if (els.ytMode) {
  els.ytMode.addEventListener("change", () => {
    const hints = {
      subtitle: "Original audio kept. Translated captions burned onto the video — fast, no voice change.",
      srt:      "Exports only the subtitle .srt file — fastest, no re-encode. Open in VLC or burn with HandBrake.",
      dub:      "Original audio replaced with AI Neural TTS voice in target language. Frame-accurate sync.",
      both:     "Best quality: TTS voice + subtitles burned on screen simultaneously. Two-pass encode.",
    };
    if (els.ytModeHint) els.ytModeHint.textContent = hints[els.ytMode.value] || "";
  });
}

// ---------------------------------------------------------------------------
// YouTube source toggle — "YouTube URL" vs "Local Video File"
// ---------------------------------------------------------------------------
function setYtSource(src) {
  ytSource = src;
  const isLocal = src === "local";

  if (els.ytSrcYoutube) {
    els.ytSrcYoutube.classList.toggle("active", !isLocal);
    els.ytSrcYoutube.setAttribute("aria-selected", String(!isLocal));
  }
  if (els.ytSrcLocal) {
    els.ytSrcLocal.classList.toggle("active", isLocal);
    els.ytSrcLocal.setAttribute("aria-selected", String(isLocal));
  }
  if (els.ytUrlSection)   els.ytUrlSection.hidden   =  isLocal;
  if (els.ytLocalSection) els.ytLocalSection.hidden = !isLocal;

  // Reset local file state when switching back to URL mode.
  if (!isLocal) {
    ytLocalFile = null;
    if (els.ytLocalTitle) els.ytLocalTitle.textContent = "Drop a video or click to choose";
    if (els.ytLocalSub)   els.ytLocalSub.textContent   = "Accepts .mp4 · .mkv · .mov · .avi · .webm — up to 3 GB";
    if (els.ytLocalDropzone) els.ytLocalDropzone.classList.remove("loaded");
  }

  clearStatus();
  refreshRunState();
}

if (els.ytSrcYoutube) els.ytSrcYoutube.addEventListener("click", () => setYtSource("youtube"));
if (els.ytSrcLocal)   els.ytSrcLocal.addEventListener("click",   () => setYtSource("local"));

// Local video file picker — click to open.
if (els.ytLocalDropzone) {
  // Prevent default label-for behaviour from double-triggering.
  els.ytLocalDropzone.addEventListener("click", (e) => {
    if (e.target !== els.ytLocalFile) els.ytLocalFile && els.ytLocalFile.click();
  });
}
if (els.ytLocalFile) {
  els.ytLocalFile.addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    ytLocalFile = f;
    if (els.ytLocalTitle) els.ytLocalTitle.textContent = f.name;
    if (els.ytLocalSub)   els.ytLocalSub.textContent   = formatBytes(f.size);
    if (els.ytLocalDropzone) els.ytLocalDropzone.classList.add("loaded");
    clearStatus();
    refreshRunState();
  });
}
// Drag-and-drop onto the local dropzone.
if (els.ytLocalDropzone) {
  ["dragenter", "dragover"].forEach((ev) =>
    els.ytLocalDropzone.addEventListener(ev, (e) => {
      e.preventDefault(); els.ytLocalDropzone.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    els.ytLocalDropzone.addEventListener(ev, (e) => {
      e.preventDefault(); els.ytLocalDropzone.classList.remove("drag");
    })
  );
  els.ytLocalDropzone.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    ytLocalFile = f;
    if (els.ytLocalTitle) els.ytLocalTitle.textContent = f.name;
    if (els.ytLocalSub)   els.ytLocalSub.textContent   = formatBytes(f.size);
    els.ytLocalDropzone.classList.add("loaded");
    clearStatus();
    refreshRunState();
  });
}

// Tab listeners
els.tabEncrypt.addEventListener("click",  () => setMode("encrypt"));
els.tabDecrypt.addEventListener("click",  () => setMode("decrypt"));
els.tabConvert.addEventListener("click",  () => setMode("convert"));
els.tabEffects.addEventListener("click",  () => setMode("effects"));
els.tabVideo.addEventListener("click",    () => setMode("video"));
els.tabStudio.addEventListener("click",   () => setMode("studio"));
els.tabGeo.addEventListener("click",      () => setMode("geo"));
els.tabYoutube.addEventListener("click",  () => setMode("youtube"));

// ---------------------------------------------------------------------------
// Geo Map — Leaflet-based GPS readout
// ---------------------------------------------------------------------------
const Geo = (() => {
  let map = null, marker = null, satLayer = null, streetLayer = null;

  function ensureMap() {
    if (map || typeof L === "undefined") return;
    map = L.map(els.geoMap, { zoomControl: true, attributionControl: true });
    satLayer = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, attribution: "Imagery © Esri" }
    );
    streetLayer = L.tileLayer(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { maxZoom: 19, attribution: "© OpenStreetMap" }
    );
    satLayer.addTo(map);
    L.control.layers({ "Satellite": satLayer, "Street": streetLayer }, {}, { position: "topright" }).addTo(map);
    map.setView([20, 0], 2);
  }

  function pinIcon() {
    return L.divIcon({
      className: "geo-pin",
      html: '<svg width="30" height="40" viewBox="0 0 30 40" xmlns="http://www.w3.org/2000/svg"><path d="M15 0C7 0 1 6 1 14c0 10 14 26 14 26s14-16 14-26C29 6 23 0 15 0z" fill="#e0945a" stroke="#1a1208" stroke-width="1.5"/><circle cx="15" cy="14" r="5" fill="#1a1208"/></svg>',
      iconSize: [30, 40], iconAnchor: [15, 40],
    });
  }

  async function locate(file) {
    els.geoCoords.hidden = true;
    els.geoMap.hidden = true;
    els.geoStatus.hidden = false;
    els.geoStatus.className = "geo-status working";
    els.geoStatus.textContent = "Reading location metadata…";

    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch("/api/geolocate", { method: "POST", body: form });
      const data = await res.json();
      if (!data.ok) {
        els.geoStatus.className = "geo-status err";
        els.geoStatus.textContent = data.error || "Could not read the file.";
        return;
      }
      if (!data.found) {
        els.geoStatus.className = "geo-status err";
        els.geoStatus.textContent = data.note || "No GPS location found in this file.";
        return;
      }

      els.geoStatus.hidden = true;
      els.geoLat.textContent = data.lat;
      els.geoLon.textContent = data.lon;
      els.geoGmaps.href = `https://www.google.com/maps?q=${data.lat},${data.lon}`;
      els.geoGmaps.hidden = false;
      els.geoCoords.hidden = false;
      els.geoMap.hidden = false;

      ensureMap();
      const latlng = [data.lat, data.lon];
      map.setView(latlng, 15);
      if (marker) marker.remove();
      marker = L.marker(latlng, { icon: pinIcon() }).addTo(map)
        .bindPopup(`${data.lat}, ${data.lon}`).openPopup();
      setTimeout(() => map.invalidateSize(), 100);
    } catch (err) {
      els.geoStatus.className = "geo-status err";
      els.geoStatus.textContent = "Error reading location: " + err.message;
    }
  }

  return { locate };
})();

// ---------------------------------------------------------------------------
// Studio — canvas thumbnail/text editor
// ---------------------------------------------------------------------------
const Studio = (() => {
  const canvas = els.studioCanvas;
  const ctx = canvas ? canvas.getContext("2d") : null;
  let img = null;
  let textY = null;
  let dragging = false;

  function loadImage(file) {
    const url = URL.createObjectURL(file);
    const im = new Image();
    im.onload = () => {
      img = im;
      textY = null;
      const maxW = 900;
      const scale = Math.min(maxW / im.naturalWidth, 1);
      canvas.width = Math.round(im.naturalWidth * scale);
      canvas.height = Math.round(im.naturalHeight * scale);
      els.studioStage.hidden = false;
      els.studioActions.hidden = false;
      els.studioHint.textContent = "Drag the text on the image to reposition it. Edit the text and sliders above.";
      draw();
      URL.revokeObjectURL(url);
    };
    im.src = url;
  }

  function currentTextYFraction() {
    if (textY !== null) return textY;
    const pos = els.textPos.value;
    return pos === "top" ? 0.14 : pos === "middle" ? 0.5 : 0.86;
  }

  function draw() {
    if (!ctx || !img) return;
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.drawImage(img, 0, 0, W, H);

    const lines = els.studioText.value.split("\n");
    const fontPx = parseInt(els.fontSize.value, 10) * (W / 900);
    const lineH = fontPx * 1.2;
    const align = els.textAlign.value;
    const yFrac = currentTextYFraction();
    const blockH = lineH * lines.length;
    let y = H * yFrac - blockH / 2 + lineH / 2;
    const x = align === "left" ? W * 0.05 : align === "right" ? W * 0.95 : W / 2;

    if (els.bandToggle.checked) {
      ctx.fillStyle = "rgba(0,0,0,0.45)";
      ctx.fillRect(0, y - lineH * 0.75, W, blockH + lineH * 0.4);
    }

    ctx.font = `700 ${fontPx}px "Space Grotesk", system-ui, sans-serif`;
    ctx.textAlign = align;
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    ctx.lineWidth = Math.max(2, fontPx * 0.08);
    ctx.strokeStyle = els.strokeColor.value;
    ctx.fillStyle = els.textColor.value;

    for (const line of lines) {
      ctx.strokeText(line, x, y);
      ctx.fillText(line, x, y);
      y += lineH;
    }
  }

  function download(type) {
    if (!img) return;
    const mime = type === "jpg" ? "image/jpeg" : "image/png";
    const url = canvas.toDataURL(mime, 0.92);
    const a = document.createElement("a");
    a.href = url;
    a.download = "thumbnail." + (type === "jpg" ? "jpg" : "png");
    document.body.appendChild(a); a.click(); a.remove();
  }

  function pointerFrac(e) {
    const r = canvas.getBoundingClientRect();
    return (e.clientY - r.top) / r.height;
  }
  if (canvas) {
    canvas.addEventListener("pointerdown", (e) => { dragging = true; textY = pointerFrac(e); draw(); canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => { if (dragging) { textY = Math.max(0.05, Math.min(0.95, pointerFrac(e))); draw(); } });
    canvas.addEventListener("pointerup", (e) => { dragging = false; try { canvas.releasePointerCapture(e.pointerId); } catch {} });
  }

  ["input", "change"].forEach((ev) => {
    [els.studioText, els.fontSize, els.textColor, els.strokeColor,
     els.bandToggle, els.textAlign, els.textPos].forEach((el) => el && el.addEventListener(ev, () => {
      if (els.fsVal) els.fsVal.textContent = els.fontSize.value;
      if (el === els.textPos) textY = null;
      draw();
    }));
  });
  if (els.studioPng) els.studioPng.addEventListener("click", () => download("png"));
  if (els.studioJpg) els.studioJpg.addEventListener("click", () => download("jpg"));

  return { loadImage };
})();

els.convertType.addEventListener("change", () => { updateConvertHint(); clearStatus(); });
els.depthSlider.addEventListener("input", () => {
  els.depthValue.textContent = parseFloat(els.depthSlider.value).toFixed(2);
});
if (els.watermarkOpacity) {
  els.watermarkOpacity.addEventListener("input", () => {
    els.watermarkOpacityVal.textContent = parseFloat(els.watermarkOpacity.value).toFixed(2);
  });
}
// Enable run button as user types a YouTube URL.
if (els.ytUrl) {
  els.ytUrl.addEventListener("input", refreshRunState);
}

// ---------------------------------------------------------------------------
// File selection (click + drag/drop)
// ---------------------------------------------------------------------------
els.fileInput.addEventListener("change", (e) => {
  if (e.target.files.length) setFile(e.target.files[0]);
});

["dragenter", "dragover"].forEach((evt) =>
  els.dropzone.addEventListener(evt, (e) => { e.preventDefault(); els.dropzone.classList.add("drag"); })
);
["dragleave", "drop"].forEach((evt) =>
  els.dropzone.addEventListener(evt, (e) => { e.preventDefault(); els.dropzone.classList.remove("drag"); })
);
els.dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
});

function setFile(file) {
  selectedFile = file;
  els.dropzone.classList.add("loaded");
  els.dzTitle.textContent = file.name;
  els.dzSub.textContent = formatBytes(file.size);
  if (mode === "studio") Studio.loadImage(file);
  if (mode === "geo")    Geo.locate(file);
  refreshRunState();
}

// ---------------------------------------------------------------------------
// Passphrase
// ---------------------------------------------------------------------------
els.passphrase.addEventListener("input", refreshRunState);
els.togglePass.addEventListener("click", () => {
  const showing = els.passphrase.type === "text";
  els.passphrase.type = showing ? "password" : "text";
  els.togglePass.textContent = showing ? "view" : "hide";
});

function refreshRunState() {
  const convertLike = mode === "convert" || mode === "effects" || mode === "video";

  if (mode === "youtube") {
    if (ytSource === "local") {
      // Local video mode: enabled when a video file is chosen.
      els.runBtn.disabled = !ytLocalFile;
      if (els.runLabel) els.runLabel.textContent = "▶ Translate video";
    } else {
      // YouTube URL mode: enabled when the URL field has content.
      const hasUrl = els.ytUrl && els.ytUrl.value.trim().length > 0;
      els.runBtn.disabled = !hasUrl;
      if (els.runLabel) els.runLabel.textContent = "▶ Translate video";
    }
    return;
  }

  if (convertLike) {
    els.runBtn.disabled = !selectedFile;
    if (els.fxPreviewBtn) els.fxPreviewBtn.disabled = !(selectedFile && mode === "effects");
    const isRelief = mode === "convert" &&
      (els.convertType.value === "img2obj" || els.convertType.value === "img2stl");
    if (els.previewBtn) els.previewBtn.disabled = !(selectedFile && isRelief);
    const canImgPreview = mode === "convert" &&
      (els.convertType.value.startsWith("med2") || els.convertType.value === "dcm_anon" ||
       els.convertType.value.startsWith("raw2"));
    if (els.imgPreviewBtn) els.imgPreviewBtn.disabled = !(selectedFile && canImgPreview);
  } else {
    els.runBtn.disabled = !(selectedFile && els.passphrase.value.length > 0);
  }
}

function activeConversionType() {
  if (mode === "effects") return els.effectsType.value;
  if (mode === "video")   return els.videoType.value;
  return els.convertType.value;
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------
els.runBtn.addEventListener("click", run);

async function run() {
  if (mode === "youtube") {
    return ytSource === "local" ? runLocalVideoTranslate() : runYoutubeTranslate();
  }
  if (mode === "convert" || mode === "effects" || mode === "video") return runConvert();
  if (!selectedFile || !els.passphrase.value) return;

  els.runBtn.disabled = true;
  setStatus("working", mode === "encrypt"
    ? "Deriving root key (Argon2id), generating ephemeral DH, wrapping Data Key…"
    : "Deriving root key, reconstructing DH, verifying authentication tag…");

  const form = new FormData();
  form.append("file", selectedFile);
  form.append("passphrase", els.passphrase.value);

  const endpoint = mode === "encrypt" ? "/api/encrypt" : "/api/decrypt";

  try {
    const res = await fetch(endpoint, { method: "POST", body: form });

    if (!res.ok) {
      let msg = `Request failed (${res.status})`;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch {}
      if (res.status === 422) {
        setStatus("err", "Authentication failed. The passphrase is wrong, or the file was modified. No output was produced — the tag check prevents silent corruption.");
      } else {
        setStatus("err", msg);
      }
      els.runBtn.disabled = false;
      return;
    }

    let meta = {};
    const metaHeader = res.headers.get("X-RSI-Meta");
    if (metaHeader) { try { meta = JSON.parse(atob(metaHeader)); } catch {} }

    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"))
      || (mode === "encrypt" ? selectedFile.name + ".rsienc" : "decrypted.bin");
    triggerDownload(blob, filename);

    renderSuccess(meta, filename);
    await loadState({ animate: mode === "encrypt" });
  } catch (err) {
    setStatus("err", "Network error: " + err.message);
  } finally {
    els.runBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// YouTube Translation
// ---------------------------------------------------------------------------
async function runYoutubeTranslate() {
  const url      = els.ytUrl    ? els.ytUrl.value.trim()    : "";
  const src_lang = els.ytSrcLang ? els.ytSrcLang.value      : "pt";
  const tgt_lang = els.ytTgtLang ? els.ytTgtLang.value      : "fr";
  const ytMode   = els.ytMode   ? els.ytMode.value          : "subtitle";
  const model    = els.ytModel  ? els.ytModel.value         : "base";

  if (!url) {
    setStatus("err", "Paste a YouTube URL first.");
    return;
  }
  if (!/youtube\.com\/watch|youtu\.be\/|youtube\.com\/shorts/.test(url)) {
    setStatus("err", "That doesn't look like a YouTube link. Use youtube.com/watch?v=… or youtu.be/…");
    return;
  }

  els.runBtn.disabled = true;

  const modeLabels = {
    subtitle: "Downloading, transcribing, translating, burning subtitles…",
    srt:      "Downloading, transcribing, translating to SRT…",
    dub:      "Downloading, transcribing, translating, generating TTS audio…",
    both:     "Downloading, transcribing, translating, dubbing + burning subtitles… (2 passes — takes longer)",
  };
  setStatus("working",
    (modeLabels[ytMode] || "Processing…") +
    `<br><span style="color:var(--muted)">This takes 3–8 min for a 10-min video on VPS CPU. Please wait and don't close the page.</span>`
  );

  try {
    const res = await fetch("/api/youtube/translate", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ url, src_lang, tgt_lang, mode: ytMode, model }),
    });

    let meta = {};
    const metaHeader = res.headers.get("X-RSI-Meta");
    if (metaHeader) { try { meta = JSON.parse(atob(metaHeader)); } catch {} }

    if (!res.ok) {
      let errMsg = `Translation failed (${res.status})`;
      try { const j = await res.json(); if (j.error) errMsg = j.error; } catch {}
      setStatus("err", errMsg);
      return;
    }

    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"))
      || `youtube_${tgt_lang}_${ytMode}.${ytMode === "srt" ? "srt" : "mp4"}`;
    triggerDownload(blob, filename);

    const rows = [
      ["Output file",  filename],
      ["File size",    formatBytes(blob.size)],
      ["Segments",     meta.segments || "—"],
      ["Source lang",  meta.src_lang || src_lang],
      ["Target lang",  meta.tgt_lang || tgt_lang],
      ["Mode",         meta.mode || ytMode],
    ];
    if (meta.note) rows.push(["Note", meta.note]);

    const modeNote = {
      dub:      `Dub: original audio replaced with ${tgt_lang} TTS Neural voice (frame-accurate sync).`,
      subtitle: `Subtitle: ${tgt_lang} captions burned into video, original audio kept.`,
      both:     `Both: ${tgt_lang} TTS voice speaking + ${tgt_lang} subtitles burned on screen.`,
      srt:      "Open the .srt in VLC (Subtitle menu) or burn it with HandBrake.",
    }[ytMode] || "";

    setStatus("ok",
      `Done! Your translated video is ready: <span class="mono">${escapeHtml(filename)}</span>.` +
      (modeNote ? `<br><span style="color:var(--muted)">${modeNote}</span>` : ""),
      rows
    );
  } catch (err) {
    setStatus("err", "Network error: " + err.message +
      "<br>If the server is still processing, wait and try again — long jobs can take 10+ minutes.");
  } finally {
    els.runBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Local Video Translation — upload an existing video file and translate it
// ---------------------------------------------------------------------------
async function runLocalVideoTranslate() {
  if (!ytLocalFile) {
    setStatus("err", "Choose a video file first (📁 Local Video tab).");
    return;
  }

  const src_lang = els.ytSrcLang ? els.ytSrcLang.value : "en";
  const tgt_lang = els.ytTgtLang ? els.ytTgtLang.value : "fr";
  const ytMode   = els.ytMode    ? els.ytMode.value    : "subtitle";
  const model    = els.ytModel   ? els.ytModel.value   : "base";

  els.runBtn.disabled = true;

  const modeLabels = {
    subtitle: "Transcribing, translating, burning subtitles into video…",
    srt:      "Transcribing and translating to SRT…",
    dub:      "Transcribing, translating, generating TTS audio…",
    both:     "Transcribing, translating, dubbing + burning subtitles… (2 passes — takes longer)",
  };

  const sizeMB = (ytLocalFile.size / 1024 / 1024).toFixed(1);
  setStatus("working",
    `Uploading ${escapeHtml(ytLocalFile.name)} (${sizeMB} MB)…<br>` +
    `<span style="color:var(--muted)">${modeLabels[ytMode] || "Processing…"} ` +
    `This takes several minutes for longer videos. Do not close the page.</span>`
  );

  const form = new FormData();
  form.append("file",     ytLocalFile);
  form.append("src_lang", src_lang);
  form.append("tgt_lang", tgt_lang);
  form.append("mode",     ytMode);
  form.append("model",    model);

  try {
    const res = await fetch("/api/video/translate", { method: "POST", body: form });

    let meta = {};
    const metaHeader = res.headers.get("X-RSI-Meta");
    if (metaHeader) { try { meta = JSON.parse(atob(metaHeader)); } catch {} }

    if (!res.ok) {
      let errMsg = `Translation failed (${res.status})`;
      try { const j = await res.json(); if (j.error) errMsg = j.error; } catch {}
      setStatus("err", errMsg);
      return;
    }

    const blob     = await res.blob();
    const outExt   = ytMode === "srt" ? "srt" : "mp4";
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"))
      || `${ytLocalFile.name.replace(/\.[^.]+$/, "")}_${tgt_lang}_${ytMode}.${outExt}`;
    triggerDownload(blob, filename);

    const rows = [
      ["Input file",  ytLocalFile.name],
      ["Output file", filename],
      ["File size",   formatBytes(blob.size)],
      ["Segments",    meta.segments || "—"],
      ["Source lang", meta.src_lang || src_lang],
      ["Target lang", meta.tgt_lang || tgt_lang],
      ["Mode",        meta.mode || ytMode],
    ];
    if (meta.note) rows.push(["Note", meta.note]);

    const modeNote = {
      dub:      `Dub: audio replaced with ${tgt_lang} TTS Neural voice (frame-accurate sync).`,
      subtitle: `Subtitle: ${tgt_lang} captions burned in, original audio kept.`,
      both:     `Both: ${tgt_lang} TTS voice speaking + ${tgt_lang} subtitles burned on screen.`,
      srt:      "Open the .srt in VLC (Subtitle menu) or burn it with HandBrake.",
    }[ytMode] || "";

    setStatus("ok",
      `Done! Translated video ready: <span class="mono">${escapeHtml(filename)}</span>.` +
      (modeNote ? `<br><span style="color:var(--muted)">${modeNote}</span>` : ""),
      rows
    );
  } catch (err) {
    setStatus("err",
      "Network error: " + err.message +
      "<br>Large files take time to upload + process. If you see a timeout, try the SRT-only mode first."
    );
  } finally {
    els.runBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Text scan
// ---------------------------------------------------------------------------
async function runTextScan() {
  els.runBtn.disabled = true;
  setStatus("working", "Scanning the image pixels for text (OCR)…");
  const form = new FormData();
  form.append("file", selectedFile);
  try {
    const res = await fetch("/api/scantext", { method: "POST", body: form });
    const data = await res.json();
    if (!data.ok) { setStatus("err", data.error || "Scan failed."); return; }
    if (!data.text_found) {
      setStatus("ok", "No readable text found in the image pixels. (Nothing burned in that OCR could detect.)");
    } else if (data.risky) {
      const rows = data.warnings.map((w) => ["⚠ flag", w]);
      rows.push(["Detected text", data.text]);
      setStatus("err",
        "<b>Possible burned-in identifiers found.</b> Review before sharing — anonymizing tags does not remove text printed on the image itself.",
        rows);
    } else {
      setStatus("ok",
        "Text was found but nothing matched name/date/ID patterns. Still, review it:",
        [["Detected text", data.text]]);
    }
  } catch (err) {
    setStatus("err", "Scan error: " + err.message);
  } finally {
    els.runBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// PDF toolbox
// ---------------------------------------------------------------------------
const PDF_TOOLBOX_ENDPOINTS = {
  pdf_merge:     "/api/pdf/merge",
  pdf_split:     "/api/pdf/split",
  pdf_remove:    "/api/pdf/remove",
  pdf_extract:   "/api/pdf/extract",
  pdf_rotate:    "/api/pdf/rotate",
  pdf_pagenum:   "/api/pdf/pagenum",
  pdf_watermark: "/api/pdf/watermark",
  pdf_protect:   "/api/pdf/protect",
  pdf_unlock:    "/api/pdf/unlock",
};

function isPdfToolboxOp(type) {
  return Object.prototype.hasOwnProperty.call(PDF_TOOLBOX_ENDPOINTS, type);
}

async function runPdfTool(type) {
  const endpoint = PDF_TOOLBOX_ENDPOINTS[type];
  const form = new FormData();

  if (type === "pdf_merge") {
    const extra = els.mergeFiles ? els.mergeFiles.files : [];
    if (!extra || extra.length < 1) {
      setStatus("err", "Add at least one more PDF to merge with the main file.");
      return;
    }
    form.append("files", selectedFile);
    for (const f of extra) form.append("files", f);
  } else {
    form.append("file", selectedFile);
  }

  if (type === "pdf_split") form.append("ranges", els.pdfRanges.value.trim());
  if (type === "pdf_remove" || type === "pdf_extract") {
    const pages = els.pdfPages.value.trim();
    if (!pages) { setStatus("err", "Enter which pages (e.g. 2,5-7)."); return; }
    form.append("pages", pages);
  }
  if (type === "pdf_rotate")    form.append("degrees",  els.pdfDegrees.value);
  if (type === "pdf_pagenum")   form.append("position", els.pagenumPosition.value);
  if (type === "pdf_watermark") {
    form.append("text",    els.watermarkText.value || "CONFIDENTIAL");
    form.append("opacity", els.watermarkOpacity.value);
  }
  if (type === "pdf_protect" || type === "pdf_unlock") {
    const pw = els.pdfPassword.value;
    if (!pw) { setStatus("err", "Enter the password."); return; }
    form.append("password", pw);
  }

  els.runBtn.disabled = true;
  setStatus("working", "Working on your PDF…");

  try {
    const res = await fetch(endpoint, { method: "POST", body: form });
    if (!res.ok) {
      let msg = `Operation failed (${res.status})`;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch {}
      setStatus("err", msg);
      return;
    }
    let meta = {};
    const metaHeader = res.headers.get("X-RSI-Meta");
    if (metaHeader) { try { meta = JSON.parse(atob(metaHeader)); } catch {} }

    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition")) || "result";
    triggerDownload(blob, filename);

    const rows = [["Output", filename], ["Size", formatBytes(blob.size)]];
    if (meta.parts)             rows.push(["Split parts", meta.parts]);
    if (meta.files_merged)      rows.push(["Merged", meta.files_merged]);
    if (meta.pages_rotated !== undefined) rows.push(["Pages rotated", meta.pages_rotated]);
    if (meta.removed !== undefined)       rows.push(["Removed", meta.removed]);
    if (meta.extracted !== undefined)     rows.push(["Extracted", meta.extracted]);
    if (meta.saved_pct !== undefined)     rows.push(["Saved", meta.saved_pct + "%"]);
    setStatus("ok",
      `Done. Your download is <span class="mono">${escapeHtml(filename)}</span>.` +
      (meta.note ? `<br><span style="color:var(--muted)">${escapeHtml(meta.note)}</span>` : ""),
      rows);
  } catch (err) {
    setStatus("err", "Network error: " + err.message);
  } finally {
    els.runBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// General convert
// ---------------------------------------------------------------------------
async function runConvert() {
  if (!selectedFile) return;
  const type = activeConversionType();

  if (type === "scan_text")    return runTextScan();
  if (isPdfToolboxOp(type))    return runPdfTool(type);

  els.runBtn.disabled = true;
  setStatus("working", mode === "video"
    ? "Processing video frame-by-frame… this can take a while for longer clips."
    : "Working… this can take a few seconds for large files.");

  const form = new FormData();
  form.append("file", selectedFile);
  form.append("type", type);
  if (type === "img2obj" || type === "img2stl") {
    form.append("depth", els.depthSlider.value);
  }

  try {
    const res = await fetch("/api/convert", { method: "POST", body: form });

    if (!res.ok) {
      let msg = `Conversion failed (${res.status})`;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch {}
      setStatus("err", msg + ". Check that the file matches the conversion (PDF for PDF options, an image for 3D options).");
      els.runBtn.disabled = false;
      return;
    }

    let meta = {};
    const metaHeader = res.headers.get("X-RSI-Meta");
    if (metaHeader) { try { meta = JSON.parse(atob(metaHeader)); } catch {} }

    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition")) || "converted";
    triggerDownload(blob, filename);

    const rows = [["Output", filename], ["Size", formatBytes(blob.size)]];
    if (meta.pages)                rows.push(["Pages",         meta.pages]);
    if (meta.tables !== undefined) rows.push(["Tables found",  meta.tables]);
    if (meta.vertices)             rows.push(["Mesh vertices", meta.vertices]);
    if (meta.depth !== undefined)  rows.push(["Relief depth",  meta.depth]);
    if (meta.quality)              rows.push(["Audio quality", meta.quality]);
    if (meta.bitrate)              rows.push(["Bitrate",       meta.bitrate]);
    setStatus("ok",
      `Converted. Your download is <span class="mono">${escapeHtml(filename)}</span>.` +
      (meta.note ? `<br><span style="color:var(--muted)">${escapeHtml(meta.note)}</span>` : ""),
      rows);
  } catch (err) {
    setStatus("err", "Network error: " + err.message);
  } finally {
    els.runBtn.disabled = false;
  }
}

function renderSuccess(meta, filename) {
  if (mode === "encrypt") {
    const rows = [
      ["Output",      filename],
      ["Epoch",       meta.epoch],
      ["Chain index", meta.chain_index],
      ["Ephemeral DH",meta.ephemeral_pub || "—"],
    ];
    if (meta.rekeyed) rows.push(["DH ratchet", "epoch advanced — chain healed"]);
    setStatus("ok",
      `Encrypted and wrapped. Your download is the <span class="mono">.rsienc</span> container.`,
      rows);
  } else {
    setStatus("ok",
      `Decrypted and verified. Recovered <span class="mono">${escapeHtml(meta.original_name || filename)}</span>.`,
      [["Bytes", formatBytes(meta.bytes_out || 0)], ["Epoch", meta.epoch]]);
  }
}

// ---------------------------------------------------------------------------
// Ratchet state panel
// ---------------------------------------------------------------------------
async function loadState({ animate = false } = {}) {
  try {
    const res = await fetch("/api/state");
    const s = await res.json();
    if (!s.ok) return;

    rekeyEvery = s.dh_rekey_every || 5;
    els.epoch.textContent = s.epoch;
    els.ops.textContent = s.op_count;
    els.dhpub.textContent = s.current_dh_pub
      ? s.current_dh_pub.slice(0, 28) + "…"
      : "no operations yet";

    renderChain(s.op_count, animate);

    const intoEpoch = s.op_count % rekeyEvery;
    const remaining = intoEpoch === 0 && s.op_count > 0 ? 0 : rekeyEvery - intoEpoch;
    els.healCount.textContent = remaining === 0 ? "now" : `${remaining} op${remaining === 1 ? "" : "s"}`;
    els.epochFill.style.width = `${(intoEpoch / rekeyEvery) * 100}%`;
  } catch {
    /* leave panel as-is on transient errors */
  }
}

function renderChain(opCount, animate) {
  const MAX_LINKS = 12;
  const total = Math.min(Math.max(opCount, 1), MAX_LINKS);
  const existing = els.chain.children.length;

  els.chain.innerHTML = "";
  for (let i = 0; i < total; i++) {
    const link = document.createElement("div");
    link.className = "link";
    if (i < total - 1) link.classList.add("used");
    if (i === total - 1) link.classList.add("head");
    if (animate && i === total - 1 && total >= existing) link.classList.add("fresh");
    els.chain.appendChild(link);
  }
}

els.rekeyBtn.addEventListener("click", async () => {
  els.rekeyBtn.disabled = true;
  try {
    const res = await fetch("/api/rekey", { method: "POST" });
    const j = await res.json();
    if (j.ok) { flashChainHeal(); await loadState(); }
  } catch {}
  els.rekeyBtn.disabled = false;
});

function flashChainHeal() {
  els.chainCaption.textContent =
    "DH ratchet stepped — fresh entropy mixed in. From here, prior compromise no longer helps an attacker.";
  setTimeout(() => {
    els.chainCaption.textContent =
      "Each link is derived from the previous through a one-way function. Past keys can't be recovered from later ones.";
  }, 4200);
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------
function setStatus(kind, html, rows) {
  els.status.hidden = false;
  els.status.className = `status ${kind}`;
  let inner = `<div>${html}</div>`;
  if (rows && rows.length) {
    inner += rows
      .map(([k, v]) => `<div class="row"><span>${escapeHtml(k)}</span><b>${escapeHtml(String(v))}</b></div>`)
      .join("");
  }
  els.status.innerHTML = inner;
}
function clearStatus() {
  els.status.hidden = true;
  els.status.innerHTML = "";
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function filenameFromDisposition(disp) {
  if (!disp) return null;
  const m = /filename="?([^"]+)"?/.exec(disp);
  return m ? m[1] : null;
}

function formatBytes(n) {
  if (n < 1024)           return `${n} B`;
  if (n < 1024 * 1024)    return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------------------------------------------------------------------------
// 3D preview — Three.js turntable
// ---------------------------------------------------------------------------
const Viewer = (() => {
  let renderer, scene, camera, pivot, raf;
  let spinning = true;
  let dragging = false, lastX = 0, lastY = 0;
  let camDist = 200;

  function available() { return typeof THREE !== "undefined"; }

  function init() {
    if (renderer) return;
    const canvas = els.viewerCanvas;
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(45, 1, 0.1, 5000);

    const amb = new THREE.AmbientLight(0xffffff, 0.55);
    const key = new THREE.DirectionalLight(0xffe6c4, 0.9);
    key.position.set(1, 2, 1.5);
    const rim = new THREE.DirectionalLight(0x6fd0c0, 0.5);
    rim.position.set(-1.5, 1, -1);
    scene.add(amb, key, rim);

    pivot = new THREE.Group();
    scene.add(pivot);

    canvas.addEventListener("pointerdown", (e) => {
      dragging = true; lastX = e.clientX; lastY = e.clientY;
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointerup", (e) => {
      dragging = false;
      try { canvas.releasePointerCapture(e.pointerId); } catch {}
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dx = (e.clientX - lastX) * 0.01;
      const dy = (e.clientY - lastY) * 0.01;
      lastX = e.clientX; lastY = e.clientY;
      pivot.rotation.y += dx;
      pivot.rotation.x = Math.max(-1.4, Math.min(1.4, pivot.rotation.x + dy));
    });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      camDist = Math.max(40, Math.min(800, camDist * (1 + Math.sign(e.deltaY) * 0.1)));
      camera.position.setLength(camDist);
    }, { passive: false });

    animate();
  }

  function resize() {
    const r = els.viewer.getBoundingClientRect();
    const w = Math.max(r.width, 100), h = 260;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function parseOBJ(text) {
    const positions = [];
    const verts = [];
    const lines = text.split("\n");
    for (const line of lines) {
      if (line[0] === "v" && line[1] === " ") {
        const p = line.split(/\s+/);
        verts.push([+p[1], +p[2], +p[3]]);
      } else if (line[0] === "f" && line[1] === " ") {
        const p = line.split(/\s+/);
        for (let k = 1; k <= 3; k++) {
          const idx = parseInt(p[k], 10) - 1;
          const v = verts[idx];
          if (v) positions.push(v[0], v[1], v[2]);
        }
      }
    }
    return new Float32Array(positions);
  }

  function load(objText) {
    init();
    if (!renderer) return;
    while (pivot.children.length) pivot.remove(pivot.children[0]);

    const positions = parseOBJ(objText);
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.computeVertexNormals();
    geom.computeBoundingBox();

    const bb = geom.boundingBox;
    const cx = (bb.min.x + bb.max.x) / 2;
    const cy = (bb.min.y + bb.max.y) / 2;
    const cz = (bb.min.z + bb.max.z) / 2;
    geom.translate(-cx, -cy, -cz);

    const mat = new THREE.MeshStandardMaterial({
      color: 0xd9c4a8, roughness: 0.65, metalness: 0.1,
      side: THREE.DoubleSide, flatShading: false,
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.rotation.x = -Math.PI / 2;
    pivot.add(mesh);

    const size = new THREE.Vector3();
    bb.getSize(size);
    const radius = Math.max(size.x, size.y, size.z);
    camDist = radius * 2.2;
    camera.position.set(camDist * 0.2, camDist * 0.55, camDist * 0.8);
    camera.lookAt(0, 0, 0);

    pivot.rotation.set(0.2, 0, 0);
    els.viewer.hidden = false;
    resize();
  }

  function animate() {
    raf = requestAnimationFrame(animate);
    if (!renderer) return;
    if (spinning) pivot.rotation.y += 0.008;
    renderer.render(scene, camera);
  }

  function toggleSpin() { spinning = !spinning; return spinning; }

  return { available, load, toggleSpin, resize };
})();

async function previewMesh() {
  if (!selectedFile) return;
  if (!Viewer.available()) {
    setStatus("err", "3D library didn't load. Make sure /vendor/three.min.js is present.");
    return;
  }
  els.previewBtn.disabled = true;
  const prev = els.previewBtn.textContent;
  els.previewBtn.textContent = "loading…";

  const form = new FormData();
  form.append("file", selectedFile);
  form.append("depth", els.depthSlider.value);

  try {
    const res = await fetch("/api/preview", { method: "POST", body: form });
    if (!res.ok) {
      let msg = `preview failed (${res.status})`;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch {}
      setStatus("err", msg);
      return;
    }
    const objText = await res.text();
    Viewer.load(objText);
    clearStatus();
  } catch (err) {
    setStatus("err", "Preview error: " + err.message);
  } finally {
    els.previewBtn.textContent = prev;
    els.previewBtn.disabled = false;
  }
}
if (els.previewBtn) els.previewBtn.addEventListener("click", previewMesh);

// ---------------------------------------------------------------------------
// Image preview (Medical / RAW)
// ---------------------------------------------------------------------------
async function previewImage() {
  if (!selectedFile) return;
  const t = els.convertType.value;
  const renderType = t.startsWith("raw2") ? "raw2png" : "med2png";

  els.imgPreviewBtn.disabled = true;
  const label = els.imgPreviewBtn.textContent;
  els.imgPreviewBtn.textContent = "rendering…";
  els.imgPreviewCap.textContent = "";

  const form = new FormData();
  form.append("file", selectedFile);
  form.append("type", renderType);
  try {
    const res = await fetch("/api/convert", { method: "POST", body: form });
    if (!res.ok) {
      let msg = `preview failed (${res.status})`;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch {}
      els.imgPreviewStage.hidden = true;
      setStatus("err", msg);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    els.imgPreview.onload = () => URL.revokeObjectURL(url);
    els.imgPreview.src = url;
    els.imgPreviewStage.hidden = false;
    els.imgPreviewCap.textContent = renderType === "raw2png"
      ? "Decoded RAW preview — click Convert file to download your chosen format."
      : "Middle-slice preview (contrast-windowed) — click Convert file to download your chosen format.";
    clearStatus();
  } catch (err) {
    setStatus("err", "Preview error: " + err.message);
  } finally {
    els.imgPreviewBtn.textContent = label;
    els.imgPreviewBtn.disabled = false;
  }
}
if (els.imgPreviewBtn) els.imgPreviewBtn.addEventListener("click", previewImage);

// ---------------------------------------------------------------------------
// Effects preview
// ---------------------------------------------------------------------------
async function previewEffect() {
  if (!selectedFile) return;
  const t = els.effectsType.value;
  els.fxPreviewBtn.disabled = true;
  const label = els.fxPreviewBtn.textContent;
  els.fxPreviewBtn.textContent = "rendering…";
  const form = new FormData();
  form.append("file", selectedFile);
  form.append("type", t);
  try {
    const res = await fetch("/api/convert", { method: "POST", body: form });
    if (!res.ok) {
      let msg = `preview failed (${res.status})`;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch {}
      els.fxPreviewStage.hidden = true;
      setStatus("err", msg);
      return;
    }
    const meta = readMeta(res);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    els.fxPreview.onload = () => URL.revokeObjectURL(url);
    els.fxPreview.src = url;
    els.fxPreviewStage.hidden = false;
    els.fxPreviewCap.textContent = (meta && meta.spot_c_simulated)
      ? `Simulated spot: ${meta.spot_c_simulated} °C (from brightness, not measured). Click Apply effect to download.`
      : "Preview — click Apply effect to download the full-resolution image.";
    clearStatus();
  } catch (err) {
    setStatus("err", "Preview error: " + err.message);
  } finally {
    els.fxPreviewBtn.textContent = label;
    els.fxPreviewBtn.disabled = false;
  }
}

function readMeta(res) {
  const h = res.headers.get("X-RSI-Meta");
  if (!h) return null;
  try { return JSON.parse(atob(h)); } catch { return null; }
}

if (els.fxPreviewBtn)  els.fxPreviewBtn.addEventListener("click", previewEffect);
if (els.effectsType)   els.effectsType.addEventListener("change", () => { els.fxPreviewStage.hidden = true; });
if (els.spinBtn)       els.spinBtn.addEventListener("click", () => {
  const on = Viewer.toggleSpin();
  els.spinBtn.textContent = on ? "⟳ spin" : "❚❚ stop";
});
window.addEventListener("resize", () => Viewer.resize && Viewer.resize());

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
setMode("encrypt");
loadState();


