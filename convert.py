#!/usr/bin/env python3
"""
convert.py — RSI Vault file-conversion worker
=============================================

Called by server.js as a subprocess. Prints a single JSON object to stdout
describing the result (or an error). The actual output file(s) are written
to the path the server passes in.

Commands:
    convert.py pdf2docx   <in.pdf>  <out.docx>
    convert.py pdf2xlsx   <in.pdf>  <out.xlsx>
    convert.py pdf2img    <in.pdf>  <out.zip>   [png|jpg]   [dpi]
    convert.py img2relief <in.img>  <out.zip>   [obj|stl]   (height-map 3D mesh)
    convert.py img2anaglyph <in.img> <out.png>              (red/cyan stereo 3D)
    convert.py youtube_translate <url> <out.mp4> [src_lang] [tgt_lang] [mode] [model]

Honest scope notes:
  * pdf2docx / pdf2xlsx are best-effort. Text-based PDFs convert well;
    scanned or graphic-heavy PDFs convert approximately.
  * "3D" here is a deterministic relief mesh (brightness -> height) or an
    anaglyph — NOT AI geometry reconstruction. Documented as such.
  * youtube_translate: Whisper transcription + Google Translate (free API) +
    edge-tts Neural voices. Quality depends on audio clarity and model size.
"""

import json
import os
import sys
import struct
import zipfile


def out(obj):
    print(json.dumps(obj))


RAW_EXTS = (".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef",
            ".x3f", ".3fr", ".rwl", ".dcr", ".mrw", ".dng", ".raw", ".srw",
            ".nrw", ".kdc")


# ---------------------------------------------------------------------------
# RAW camera formats -> standard image (via rawpy / LibRaw)
# ---------------------------------------------------------------------------
def raw_convert(in_path, out_path, fmt="jpg"):
    import rawpy
    import numpy as np
    fmt = (fmt or "jpg").lower()
    try:
        with rawpy.imread(in_path) as raw:
            bps = 16 if fmt in ("tif", "tiff") else 8
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False,
                                  output_bps=bps)
    except Exception as e:
        raise RuntimeError(f"could not decode RAW file: {e}")

    if fmt in ("tif", "tiff"):
        import imageio
        imageio.imwrite(out_path, rgb)
    else:
        from PIL import Image
        im = Image.fromarray(rgb.astype(np.uint8))
        if fmt in ("jpg", "jpeg"):
            im.save(out_path, "JPEG", quality=92)
        else:
            im.save(out_path, "PNG")
    return {"ok": True, "action": "raw_convert", "output": os.path.basename(out_path),
            "format": fmt, "bytes_out": os.path.getsize(out_path),
            "note": "RAW demosaiced with camera white balance."}


# ---------------------------------------------------------------------------
# Burned-in text detection (OCR) — warns about names/dates/IDs in the pixels
# ---------------------------------------------------------------------------
def _load_as_pil_for_ocr(in_path):
    from PIL import Image
    ext = os.path.splitext(in_path)[1].lower()
    if ext in RAW_EXTS:
        import rawpy
        import numpy as np
        with rawpy.imread(in_path) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        return Image.fromarray(rgb.astype(np.uint8))
    if ext in MED_INPUT_EXTS and ext not in STD_IMAGE_EXTS:
        import SimpleITK as sitk
        import numpy as np
        arr = sitk.GetArrayFromImage(sitk.ReadImage(in_path)).astype(np.float32)
        while arr.ndim > 2:
            arr = arr[arr.shape[0] // 2]
        lo, hi = np.percentile(arr, [1, 99])
        if hi <= lo:
            hi, lo = arr.max(), arr.min()
        arr = np.clip((arr - lo) / (hi - lo + 1e-6), 0, 1) * 255
        return Image.fromarray(arr.astype("uint8"))
    return Image.open(in_path)


def scan_burned_text(in_path):
    import re
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError("pytesseract not installed (needs the tesseract-ocr binary too)")

    img = _load_as_pil_for_ocr(in_path)
    raw_text = pytesseract.image_to_string(img)
    text = " ".join(raw_text.split())

    warnings = []
    if re.search(r"\b\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}\b", text):
        warnings.append("date-like pattern (dd/mm/yyyy)")
    if re.search(r"\b(19|20)\d{6}\b", text):
        warnings.append("YYYYMMDD date")
    if re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", text):
        warnings.append("name-like (two capitalized words)")
    if re.search(r"\b[A-Z]{2,}\s*\^\s*[A-Z]{2,}", text) or re.search(r"\b[A-Z]{3,}\s+[A-Z]{3,}\b", text):
        warnings.append("uppercase name-like (e.g. DOE^JOHN)")
    if re.search(r"\b(MRN|patient|DOB|name|ID)\b", text, re.I):
        warnings.append("PHI keyword (MRN/patient/DOB/name/ID)")
    if re.search(r"\b[A-Z]{2,4}-?\d{4,}\b", text):
        warnings.append("ID-like alphanumeric")

    found = bool(text.strip())
    return {"ok": True, "action": "scan_text", "text_found": found,
            "text": text[:600], "warnings": sorted(set(warnings)),
            "risky": bool(warnings),
            "note": "Detected text is what OCR could read from the pixels. "
                    "Review it before sharing — anonymizing tags does not remove "
                    "text printed onto the image itself."}


# ---------------------------------------------------------------------------
# Medical imaging formats (DICOM / NIfTI / NRRD / MetaImage / Analyze)
# Pure format conversion + slice preview — NOT diagnostic in any way.
# ---------------------------------------------------------------------------
MED_INPUT_EXTS = (".dcm", ".dicom", ".nii", ".gz", ".nrrd", ".mha", ".mhd",
                  ".hdr", ".img")
STD_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")


def _read_medical(in_path):
    """Read a medical volume/slice, or wrap a standard image as grayscale."""
    import SimpleITK as sitk
    ext = os.path.splitext(in_path)[1].lower()
    if ext in STD_IMAGE_EXTS:
        from PIL import Image
        import numpy as np
        arr = np.asarray(Image.open(in_path).convert("L"))
        return sitk.GetImageFromArray(arr)
    return sitk.ReadImage(in_path)


def medconvert(in_path, out_path, target):
    import SimpleITK as sitk
    target = (target or "png").lower()
    img = _read_medical(in_path)

    if target == "png":
        return _medical_to_png(img, out_path)

    # Pair formats (Analyze .hdr/.img, MHD .mhd/.raw) are zipped.
    if target in ("hdr", "mhd"):
        import tempfile
        import zipfile
        work = tempfile.mkdtemp(prefix="rsi_med_")
        stem = os.path.join(work, "volume")
        header = f"{stem}.{target}"
        if target == "hdr":
            img = sitk.Cast(img, sitk.sitkInt16)
        sitk.WriteImage(img, header)
        produced = [f for f in os.listdir(work)]
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in produced:
                z.write(os.path.join(work, f), arcname=f)
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        return {"ok": True, "action": "medconvert", "target": target,
                "output": os.path.basename(out_path),
                "files": produced, "bytes_out": os.path.getsize(out_path),
                "note": f"{target.upper()} is a header+data pair — both files are in the zip."}

    # Single-file targets: nii, nrrd, mha, dcm
    if target == "dcm":
        # DICOM needs an integer pixel type.
        img = sitk.Cast(img, sitk.sitkUInt16)
    sitk.WriteImage(img, out_path)
    return {"ok": True, "action": "medconvert", "target": target,
            "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Format conversion only — pixel data preserved, not interpreted."}


def _medical_to_png(img, out_path):
    import SimpleITK as sitk
    import numpy as np
    from PIL import Image
    arr = sitk.GetArrayFromImage(img).astype(np.float32)
    # Reduce to a single 2D slice for preview.
    while arr.ndim > 2:
        arr = arr[arr.shape[0] // 2]
    # Window to 1–99th percentile for visible contrast, scale to 8-bit.
    lo, hi = np.percentile(arr, [1, 99])
    if hi <= lo:
        hi = arr.max(); lo = arr.min()
    arr = np.clip((arr - lo) / (hi - lo + 1e-6), 0, 1) * 255
    Image.fromarray(arr.astype("uint8")).save(out_path)
    return {"ok": True, "action": "med2png", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Middle slice rendered for preview (contrast-windowed)."}


# ---------------------------------------------------------------------------
# DICOM anonymizer — strip direct patient identifiers (PHI) from tags.
# Follows the spirit of the DICOM Basic Confidentiality Profile. This removes
# TAG-level PHI; it does NOT remove text burned into pixels (see note).
# ---------------------------------------------------------------------------
_PHI_BLANK = [
    "PatientID", "PatientBirthDate", "PatientBirthTime", "PatientAddress",
    "PatientTelephoneNumbers", "PatientMotherBirthName", "OtherPatientIDs",
    "OtherPatientNames", "ReferringPhysicianName", "ReferringPhysicianAddress",
    "ReferringPhysicianTelephoneNumbers", "PhysiciansOfRecord",
    "PerformingPhysicianName", "NameOfPhysiciansReadingStudy", "OperatorsName",
    "InstitutionName", "InstitutionAddress", "InstitutionalDepartmentName",
    "StationName", "AccessionNumber", "DeviceSerialNumber", "StudyID",
    "RequestingPhysician", "RequestingService", "IssuerOfPatientID",
    "MilitaryRank", "BranchOfService", "PatientInsurancePlanCodeSequence",
]
_PHI_DATES = ["StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate",
              "InstanceCreationDate", "PerformedProcedureStepStartDate",
              "PatientBirthDate"]
_PHI_TIMES = ["StudyTime", "SeriesTime", "AcquisitionTime", "ContentTime",
              "InstanceCreationTime", "PerformedProcedureStepStartTime"]


def dicom_anonymize(in_path, out_path):
    import pydicom
    try:
        ds = pydicom.dcmread(in_path, force=True)
    except Exception as e:
        raise RuntimeError(f"not a readable DICOM file: {e}")

    changed = []

    if "PatientName" in ds:
        ds.PatientName = "ANONYMIZED"
        changed.append("PatientName")

    for tag in _PHI_BLANK:
        if tag in ds and str(ds.data_element(tag).value):
            ds.data_element(tag).value = ""
            changed.append(tag)

    for tag in _PHI_DATES:
        if tag in ds and str(ds.data_element(tag).value):
            ds.data_element(tag).value = "19000101"
            changed.append(tag)

    for tag in _PHI_TIMES:
        if tag in ds and str(ds.data_element(tag).value):
            ds.data_element(tag).value = "000000"
            changed.append(tag)

    ds.remove_private_tags()
    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "RSI Vault tag-level anonymizer"

    ds.save_as(out_path)
    return {"ok": True, "action": "dcm_anon", "output": os.path.basename(out_path),
            "removed_count": len(set(changed)),
            "removed": sorted(set(changed)),
            "bytes_out": os.path.getsize(out_path),
            "note": "Tag-level PHI removed. This does NOT erase text burned into "
                    "the image pixels — check the image visually before sharing."}


# ---------------------------------------------------------------------------
# Geolocation — extract GPS coordinates from image EXIF or video metadata
# ---------------------------------------------------------------------------
def _image_gps(path):
    try:
        import exifread
    except ImportError:
        return None
    with open(path, "rb") as f:
        tags = exifread.process_file(f, details=False)
    lat = tags.get("GPS GPSLatitude")
    lat_ref = tags.get("GPS GPSLatitudeRef")
    lon = tags.get("GPS GPSLongitude")
    lon_ref = tags.get("GPS GPSLongitudeRef")
    if not (lat and lon and lat_ref and lon_ref):
        return None

    def to_deg(vals):
        d = [float(x.num) / float(x.den) for x in vals.values]
        return d[0] + d[1] / 60.0 + d[2] / 3600.0

    latd = to_deg(lat)
    lond = to_deg(lon)
    if str(lat_ref.values[0]).upper() != "N":
        latd = -latd
    if str(lon_ref.values[0]).upper() != "E":
        lond = -lond
    return (round(latd, 6), round(lond, 6))


def _parse_iso6709(s):
    import re
    m = re.findall(r"[+-]\d+(?:\.\d+)?", s or "")
    if len(m) >= 2:
        return (round(float(m[0]), 6), round(float(m[1]), 6))
    return None


def _video_gps(path):
    import shutil
    import subprocess
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, timeout=120,
        )
        data = json.loads(proc.stdout or b"{}")
    except Exception:
        return None
    tags = (data.get("format") or {}).get("tags") or {}
    for key in ("location", "com.apple.quicktime.location.ISO6709",
                "location-eng", "LOCATION"):
        if key in tags:
            coords = _parse_iso6709(tags[key])
            if coords:
                return coords
    return None


def geolocate(in_path):
    ext = os.path.splitext(in_path)[1].lower()
    image_exts = (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".webp")
    video_exts = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")

    coords = None
    source = None
    if ext in image_exts:
        coords = _image_gps(in_path)
        source = "image EXIF"
    elif ext in video_exts:
        coords = _video_gps(in_path)
        source = "video metadata"
    else:
        coords = _image_gps(in_path) or _video_gps(in_path)
        source = "metadata"

    if coords:
        return {"ok": True, "found": True, "lat": coords[0], "lon": coords[1],
                "source": source}
    return {"ok": True, "found": False,
            "note": "No GPS location is stored in this file. Social media and "
                    "screenshots usually strip it; only photos/videos taken with "
                    "location enabled (and not re-saved) keep coordinates."}


ART_DISCLAIMER = ("Artistic visual effect only. This is NOT a real thermal/x-ray "
                  "image and must NOT be used for medical diagnosis of any kind.")


# ---------------------------------------------------------------------------
# Image -> stylized THERMAL effect
# ---------------------------------------------------------------------------
def _iron_lut():
    import numpy as np
    stops = [
        (0.00, (10, 0, 20)),
        (0.10, (50, 0, 70)),
        (0.20, (110, 5, 110)),
        (0.32, (180, 20, 90)),
        (0.45, (225, 60, 45)),
        (0.58, (245, 110, 15)),
        (0.72, (255, 165, 5)),
        (0.86, (255, 220, 50)),
        (1.00, (255, 255, 235)),
    ]
    lut = np.zeros((256, 3), dtype=np.uint8)
    idx = [int(s[0] * 255) for s in stops]
    for c in range(3):
        lut[:, c] = np.interp(np.arange(256), idx, [s[1][c] for s in stops]).astype(np.uint8)
    return lut


def _thermal_lut():
    return _iron_lut()


def _load_font(size):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def image_thermal(in_path, out_path):
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
    import numpy as np

    img = Image.open(in_path).convert("L")
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    eq = ImageOps.equalize(img)
    img = Image.blend(img, eq, 0.35)
    gray = np.asarray(img).astype(np.float32)
    lo, hi = np.percentile(gray, [2, 98])
    norm = np.clip((gray - lo) / (hi - lo + 1e-6), 0, 1)
    rng = np.random.default_rng(7)
    norm = np.clip(norm + rng.normal(0, 0.012, norm.shape), 0, 1)

    lut = _iron_lut()
    rgb = lut[(norm * 255).astype("uint8")]
    im = Image.fromarray(rgb, "RGB")
    W, H = im.size
    draw = ImageDraw.Draw(im)

    t_min, t_max = 21.0, 35.0
    font = _load_font(max(14, W // 40))
    small = _load_font(max(12, W // 52))

    bar_w = max(12, W // 36)
    bar_h = H // 2
    bx = W - bar_w - int(W * 0.03)
    by = H // 4
    for i in range(bar_h):
        frac = 1 - i / bar_h
        c = tuple(int(v) for v in lut[int(frac * 255)])
        draw.line([(bx, by + i), (bx + bar_w, by + i)], fill=c)
    draw.rectangle([bx, by, bx + bar_w, by + bar_h], outline=(255, 255, 255), width=1)
    draw.text((bx - 2, by - font.size - 4), f"{t_max:.0f}", fill="white", font=font)
    draw.text((bx - 2, by + bar_h + 4), f"{t_min:.0f}", fill="white", font=font)

    cx, cy = W // 2, H // 2
    r = max(10, W // 45)
    for dx, dy in [(-r, 0), (r, 0), (0, -r), (0, r)]:
        draw.line([(cx, cy), (cx + dx, cy + dy)], fill="white", width=1)
    draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], outline="white", width=1)
    spot_norm = float(norm[min(cy, norm.shape[0] - 1), min(cx, norm.shape[1] - 1)])
    spot_t = t_min + spot_norm * (t_max - t_min)

    pad = int(W * 0.012)
    label = f"Spot {spot_t:.1f}"
    tw = draw.textlength(label, font=font)
    draw.rectangle([pad, pad, pad * 2 + tw + font.size, pad + font.size + 8],
                   fill=(0, 0, 0))
    draw.text((pad + 4, pad + 4), label, fill="white", font=font)
    draw.text((pad + 6 + tw, pad + 2), "°C", fill="white", font=small)
    draw.text((pad, H - small.size - pad), "SIMULATED — not real temperatures",
              fill=(255, 255, 255), font=small)

    im.save(out_path)
    return {"ok": True, "action": "img_thermal", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "spot_c_simulated": round(spot_t, 1),
            "note": "Thermal-STYLE effect with SIMULATED temperatures derived from "
                    "brightness. " + ART_DISCLAIMER}


# ---------------------------------------------------------------------------
# Image -> deep glowing "X-ray" render
# ---------------------------------------------------------------------------
def image_xray(in_path, out_path):
    from PIL import Image, ImageOps, ImageFilter, ImageChops
    import numpy as np

    img = Image.open(in_path).convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)

    body = img.point(lambda p: int(p * 0.30))
    coarse = img.filter(ImageFilter.FIND_EDGES).point(lambda p: min(255, int(p * 1.7)))
    detail = (img.filter(ImageFilter.EDGE_ENHANCE_MORE)
                 .filter(ImageFilter.FIND_EDGES)
                 .point(lambda p: min(255, int(p * 0.9))))
    edges = ImageChops.add(coarse, detail)
    glow = ImageChops.add(edges.filter(ImageFilter.GaussianBlur(2)),
                          edges.filter(ImageFilter.GaussianBlur(6)))
    glow = glow.point(lambda p: int(p * 0.7))

    lum = ImageChops.add(body, ImageChops.add(edges, glow))
    lum = np.asarray(lum).astype(np.float32) / 255.0
    lum = np.clip((lum - 0.06) / 0.94, 0, 1)

    r = lum * 0.09
    g = lum * 0.44
    b = np.clip(lum * 1.38, 0, 1)
    white_lift = np.clip((lum - 0.78) / 0.22, 0, 1) * 0.6
    r = np.clip(r + white_lift * 0.65, 0, 1)
    g = np.clip(g + white_lift, 0, 1)
    b = np.clip(b + white_lift * 0.4, 0, 1)
    rgb = (np.stack([r, g, b], axis=-1) * 255).astype("uint8")

    Image.fromarray(rgb, "RGB").save(out_path)
    return {"ok": True, "action": "img_xray", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Glowing blue X-ray-STYLE render (multi-scale edge bloom + "
                    "translucency on true black). " + ART_DISCLAIMER}


# ---------------------------------------------------------------------------
# Video effects (ffmpeg single-pass)
# ---------------------------------------------------------------------------
def _run_ffmpeg(in_path, out_path, vf):
    _ffmpeg(["-i", in_path, "-vf", vf, "-an",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path], timeout=600)


def _ffmpeg(args, timeout=600):
    """Run ffmpeg with a full argument list (input/output included)."""
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg is required for video/audio. "
                           "Install with:  sudo apt install ffmpeg")
    proc = subprocess.run([ff, "-y", *args], capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " +
                           (proc.stderr.decode(errors="replace")[-300:] or "unknown"))


def video_to_mp3(in_path, out_path, quality="enhanced"):
    """
    Extract and enhance audio from video to MP3.

    Why videos sound quiet after basic conversion:
      Most video audio is mastered at broadcast loudness (-23 LUFS).
      A plain ffmpeg -q:a 2 extraction keeps that level — quiet on speakers.

    Three quality modes:

    standard  — 192k CBR + loudnorm normalization only.
                Fast. Brings quiet audio to a consistent level.

    enhanced  — 320k CBR + loudnorm + dynamic compression + presence boost.
                Recommended. Makes voices clear and punchy, music full.
                Audio filter chain:
                  loudnorm   → normalize to -14 LUFS (loud broadcast level)
                  acompressor → compress dynamic range, raise quiet parts
                  equalizer  → +3dB at 3kHz (voice presence / clarity)
                  volume     → +3dB final boost
                  alimiter   → brick-wall limiter, prevents clipping

    max       — 320k CBR + heavy loudnorm + heavy compression + +6dB boost.
                Absolute maximum loudness. Ideal for noisy environments.
                Uses a very loud -9 LUFS target + high-ratio compressor.
    """
    MODES = {
        "standard": {
            "bitrate": "192k",
            "af": (
                "loudnorm=I=-14:LRA=11:TP=-1"          # normalize to -14 LUFS
            ),
            "note": "Normalized to -14 LUFS (192k). Good for music and speech.",
        },
        "enhanced": {
            "bitrate": "320k",
            "af": ",".join([
                "loudnorm=I=-14:LRA=7:TP=-1",           # loud normalization
                "acompressor=threshold=-20dB:ratio=4"   # compress dynamic range
                ":attack=5:release=50:makeup=4dB",      # raise quiet parts +4dB
                "equalizer=f=3000:width_type=o"         # presence boost at 3kHz
                ":width=2:g=3",                         # +3dB clarity
                "volume=3dB",                           # final volume push
                "alimiter=limit=-0.5dB:attack=5"       # prevent clipping
                ":release=50",
            ]),
            "note": "Enhanced: loudnorm + compression + presence boost (320k). "
                    "Clear, loud, and punchy audio.",
        },
        "max": {
            "bitrate": "320k",
            "af": ",".join([
                "loudnorm=I=-9:LRA=3:TP=-0.5",         # very loud normalization
                "acompressor=threshold=-25dB:ratio=8"   # heavy compression
                ":attack=3:release=30:makeup=8dB",      # +8dB gain makeup
                "highpass=f=60",                        # cut low rumble
                "equalizer=f=3000:width_type=o"         # strong presence boost
                ":width=2:g=5",                         # +5dB at 3kHz
                "equalizer=f=10000:width_type=o"        # air band boost
                ":width=2:g=3",                         # +3dB at 10kHz
                "volume=6dB",                           # +6dB final push
                "alimiter=limit=-0.1dB:attack=3"       # hard limiter
                ":release=30",
            ]),
            "note": "MAXIMUM loudness: -9 LUFS target + heavy compression + "
                    "+6dB boost (320k). Use for noisy environments.",
        },
    }

    cfg = MODES.get(quality, MODES["enhanced"])

    _ffmpeg([
        "-i",  in_path,
        "-vn",                          # no video stream
        "-af", cfg["af"],               # audio enhancement chain
        "-c:a", "libmp3lame",
        "-b:a", cfg["bitrate"],         # constant bitrate
        "-ar", "44100",                 # 44.1 kHz sample rate
        "-ac", "2",                     # stereo
        "-id3v2_version", "3",          # ID3v2.3 tags (widest compatibility)
        out_path,
    ], timeout=7200)

    return {
        "ok":       True,
        "action":   "vid2mp3",
        "quality":  quality,
        "bitrate":  cfg["bitrate"],
        "output":   os.path.basename(out_path),
        "bytes_out": os.path.getsize(out_path),
        "note":     cfg["note"],
    }


def video_to_youtube_mp4(in_path, out_path):
    _ffmpeg(["-i", in_path, "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             "-pix_fmt", "yuv420p", out_path], timeout=14400)
    return {"ok": True, "action": "vid2ytmp4", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Re-encoded to YouTube-ready H.264/AAC MP4."}


def video_to_webm(in_path, out_path):
    _ffmpeg(["-i", in_path, "-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0",
             "-deadline", "good", "-cpu-used", "4", "-row-mt", "1",
             "-c:a", "libopus", out_path], timeout=14400)
    return {"ok": True, "action": "vid2webm", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Re-encoded to WebM (VP9/Opus). Large videos take a while."}


def image_to_image(in_path, out_path, fmt="png"):
    from PIL import Image
    fmt = (fmt or "png").lower()
    img = Image.open(in_path)
    if fmt in ("jpg", "jpeg"):
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, img).convert("RGB")
        else:
            img = img.convert("RGB")
        img.save(out_path, "JPEG", quality=92)
    else:
        img.save(out_path, "PNG")
    return {"ok": True, "action": "img2img", "output": os.path.basename(out_path),
            "format": fmt, "bytes_out": os.path.getsize(out_path)}


def image_to_docx(in_path, out_path):
    from docx import Document
    from docx.shared import Inches
    from PIL import Image

    doc = Document()
    with Image.open(in_path) as im:
        w, h = im.size
    max_w_in = 6.0
    width = Inches(max_w_in)
    doc.add_picture(in_path, width=width)
    doc.save(out_path)
    _normalize_docx(out_path)
    return {"ok": True, "action": "img2docx", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Image embedded in an editable Word document — open in Word to modify."}


def video_thermal(in_path, out_path):
    _run_ffmpeg(in_path, out_path, "format=gray,pseudocolor=preset=magma")
    return {"ok": True, "action": "vid_thermal", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path), "note": ART_DISCLAIMER}


def video_anaglyph(in_path, out_path):
    _run_ffmpeg(in_path, out_path, "rgbashift=rh=-6:bh=6")
    return {"ok": True, "action": "vid_3d", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Red/cyan 3D effect for the whole video — view with red/cyan glasses. " + ART_DISCLAIMER}


# ---------------------------------------------------------------------------
# DOCX normalization / repair
# ---------------------------------------------------------------------------
def _normalize_docx(path):
    """
    Attempt to normalize a DOCX for maximum Word compatibility.

    Two-stage process:
      Stage 1 (always runs): _make_word2007_compatible — Unicode-level
        illegal XML char stripping + attribute fixes. Runs in pure Python,
        no external tools needed. This fixes the "Caractère xml illégal"
        and "Aucune information sur l'erreur" errors on Word 2007/2010/8.1.

      Stage 2 (if LibreOffice available): full round-trip through LibreOffice
        which rebuilds the entire OOXML schema from scratch — the gold standard.
        Install:  sudo apt install libreoffice --no-install-recommends
    """
    import shutil
    import subprocess
    import tempfile

    # Stage 1 — always run Python repair first
    _make_word2007_compatible(path)

    # Stage 2 — LibreOffice round-trip (optional but strongly recommended)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return False    # Stage 1 ran; Stage 2 unavailable

    outdir = tempfile.mkdtemp(prefix="rsi_docxfix_")
    try:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "docx",
             "--outdir", outdir, path],
            capture_output=True, timeout=180,
        )
        if proc.returncode != 0:
            return False

        produced = os.path.join(
            outdir, os.path.splitext(os.path.basename(path))[0] + ".docx"
        )
        if not os.path.exists(produced):
            docs = [f for f in os.listdir(outdir) if f.lower().endswith(".docx")]
            if not docs:
                return False
            produced = os.path.join(outdir, docs[0])

        _make_word2007_compatible(produced)
        shutil.move(produced, path)
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


_ONOFF_TAGS = (
    b"b|bCs|i|iCs|caps|smallCaps|strike|dstrike|outline|shadow|emboss|imprint|"
    b"vanish|specVanish|noProof|webHidden|rtl|cs|bidi|suppressAutoHyphens|"
    b"suppressLineNumbers|suppressOverlap|keepNext|keepLines|pageBreakBefore|"
    b"widowControl|wordWrap|overflowPunct|topLinePunct|autoSpaceDE|autoSpaceDN|"
    b"contextualSpacing|mirrorIndents|adjustRightInd|snapToGrid|formProt|"
    b"noWrap|tcW|hideMark|noEndnote|titlePg|savePreviewPicture|"
    b"doNotExpandShiftReturn|gutterAtTop|mirrorMargins|printTwoOnOne|"
    b"bookFoldPrinting|bookFoldRevPrinting|autoHyphenation|doNotHyphenateCaps|"
    b"evenAndOddHeaders|noPunctuationKerning|removeWordSchemaOnSave"
)


def _make_word2007_compatible(docx_path):
    """
    Repair a DOCX so it opens cleanly in Word 2007/2010/2013 on Windows 8.1+.

    Root cause of "Caractère xml illégal" / "Aucune information sur l'erreur":
      Word 2007-2013 on Windows strictly enforces XML 1.0 — any codepoint
      outside the allowed set causes an immediate parse failure, even inside
      numbering.xml, styles.xml, settings.xml, etc.

      Previous byte-regex approach was broken: listing \xef\xbf\xbe inside a
      character class [] matches INDIVIDUAL BYTES, corrupting valid 3-byte
      UTF-8 sequences that share those bytes. Always clean at Unicode level.

    XML 1.0 legal codepoints:
      U+0009  (TAB)
      U+000A  (LF)
      U+000D  (CR)
      U+0020–U+D7FF
      U+E000–U+FFFD
      U+10000–U+10FFFF
    Everything else → stripped.

    All 7 fixes applied:
      1. Decode each XML/rels file as UTF-8, strip illegal Unicode codepoints,
         re-encode — works on ALL xml parts (document, numbering, styles, etc.)
      2. w:jc val="start|end" → "left|right"  (OOXML 2007 spelling)
      3. Boolean attrs: true→on, false→off, 1→on, 0→off
      4. w:color val must be 6-hex or "auto"
      5. w:color without val gets val="auto"
      6. Strip rsidR/rsidRPr/rsidDel tracking attrs (Word 2007 strict mode)
      7. Remove UTF-8 BOM from every XML file
    """
    import re
    import tempfile
    import zipfile
    import shutil

    # ── Fix 1: Unicode-level illegal XML 1.0 character stripper ──────────
    # Works on DECODED text (str), not bytes — this is the critical fix.
    # Matches every codepoint NOT in the XML 1.0 legal set.
    _BAD_XML_UNICODE = re.compile(
        u'[^\u0009\u000A\u000D'           # not TAB, LF, CR
        u'\u0020-\uD7FF'                  # not basic multilingual plane
        u'\uE000-\uFFFD'                  # not PUA / specials (keeps \uFFFD)
        u'\U00010000-\U0010FFFF'          # not supplementary planes
        u']',
        re.UNICODE
    )

    def _strip_illegal_xml(raw_bytes):
        """Decode bytes → strip bad chars → re-encode as UTF-8."""
        # Strip BOM first (Fix 7)
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            raw_bytes = raw_bytes[3:]
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            text = raw_bytes.decode("latin-1", errors="replace")
        cleaned = _BAD_XML_UNICODE.sub("", text)
        return cleaned.encode("utf-8")

    # ── Fixes 2-6: byte-level XML attribute patches (safe after clean) ───
    _ONOFF_TAGS_LOCAL = (
        b"b|bCs|i|iCs|caps|smallCaps|strike|dstrike|outline|shadow|emboss|imprint|"
        b"vanish|specVanish|noProof|webHidden|rtl|cs|bidi|suppressAutoHyphens|"
        b"suppressLineNumbers|suppressOverlap|keepNext|keepLines|pageBreakBefore|"
        b"widowControl|wordWrap|overflowPunct|topLinePunct|autoSpaceDE|autoSpaceDN|"
        b"contextualSpacing|mirrorIndents|adjustRightInd|snapToGrid|formProt|"
        b"noWrap|tcW|hideMark|noEndnote|titlePg|savePreviewPicture|"
        b"doNotExpandShiftReturn|gutterAtTop|mirrorMargins|printTwoOnOne|"
        b"autoHyphenation|doNotHyphenateCaps|evenAndOddHeaders|noPunctuationKerning"
    )
    _onoff_re     = re.compile(rb"<w:(?:" + _ONOFF_TAGS_LOCAL + rb")\b[^>]*/>")
    _color_val_re = re.compile(rb'(<w:color\b[^>]*\bw:val=")([^"]*)(")')
    _color_novale = re.compile(rb"<w:color\b(?![^>]*w:val=)[^>]*/>")
    _rsid_attr    = re.compile(rb'\s+w:rsid[A-Za-z]*="[^"]*"')

    def _fix_onoff(m):
        tag = m.group(0)
        for old, new in [(b'"true"', b'"on"'), (b'"false"', b'"off"'),
                         (b'"1"', b'"on"'), (b'"0"', b'"off"')]:
            tag = tag.replace(b'w:val=' + old, b'w:val=' + new)
        return tag

    def _fix_color_val(m):
        head, val, tail = m.group(1), m.group(2), m.group(3)
        if val == b"auto" or re.fullmatch(rb"[0-9A-Fa-f]{6}", val):
            return m.group(0)
        return head + b"auto" + tail

    def _fix_color_missing(m):
        return m.group(0).replace(b"<w:color ", b'<w:color w:val="auto" ', 1)

    work = None
    try:
        work = tempfile.mkdtemp(prefix="rsi_compat_")
        with zipfile.ZipFile(docx_path) as z:
            z.extractall(work)

        for root, _dirs, files in os.walk(work):
            for fn in files:
                is_xml  = fn.endswith(".xml")
                is_rels = fn.endswith(".rels")
                if not is_xml and not is_rels:
                    continue

                p = os.path.join(root, fn)
                with open(p, "rb") as f:
                    raw = f.read()
                orig = raw

                # Fix 1 + Fix 7: Unicode-level illegal char strip + BOM
                # Applied to ALL xml and rels files (document, numbering,
                # styles, settings, fontTable, header, footer, etc.)
                data = _strip_illegal_xml(raw)

                # Fixes 2–6: safe to apply after the data is clean UTF-8
                if is_xml:
                    # Fix 2 — jc alignment spelling
                    data = re.sub(
                        rb'(<w:jc\b[^>]*\bw:val=")start(")', rb"\1left\2", data)
                    data = re.sub(
                        rb'(<w:jc\b[^>]*\bw:val=")end(")',   rb"\1right\2", data)
                    # Fix 3 — boolean on/off
                    data = _onoff_re.sub(_fix_onoff, data)
                    # Fix 4 & 5 — color val
                    data = _color_val_re.sub(_fix_color_val, data)
                    data = _color_novale.sub(_fix_color_missing, data)
                    # Fix 6 — rsid tracking attrs
                    data = _rsid_attr.sub(b"", data)

                if data != orig:
                    with open(p, "wb") as f:
                        f.write(data)

        # Repack DOCX: [Content_Types].xml must be the first entry
        tmp_out = docx_path + ".tmp"
        with zipfile.ZipFile(tmp_out, "w", zipfile.ZIP_DEFLATED) as z:
            ct = os.path.join(work, "[Content_Types].xml")
            if os.path.exists(ct):
                z.write(ct, "[Content_Types].xml")
            for root, _dirs, files in os.walk(work):
                for fn in files:
                    full = os.path.join(root, fn)
                    arc  = os.path.relpath(full, work)
                    if arc == "[Content_Types].xml":
                        continue
                    z.write(full, arc)
        os.replace(tmp_out, docx_path)

    except Exception:
        pass
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def _patch_docx_color_val(docx_path):
    """Alias — runs the full Word 2007 compatibility repair."""
    _make_word2007_compatible(docx_path)


# ---------------------------------------------------------------------------
# DOCX / XLSX -> PDF
# ---------------------------------------------------------------------------
def office_to_pdf(in_path, out_path):
    import shutil
    import subprocess
    import tempfile

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError(
            "LibreOffice is required for Word/Excel → PDF. "
            "Install it with:  sudo apt install libreoffice"
        )

    outdir = tempfile.mkdtemp(prefix="rsi_pdf_")
    try:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", outdir, in_path],
            capture_output=True, timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "LibreOffice failed: " + (proc.stderr.decode(errors="replace")[:300] or "unknown error")
            )

        produced = os.path.join(
            outdir, os.path.splitext(os.path.basename(in_path))[0] + ".pdf"
        )
        if not os.path.exists(produced):
            pdfs = [f for f in os.listdir(outdir) if f.lower().endswith(".pdf")]
            if not pdfs:
                raise RuntimeError("conversion produced no PDF")
            produced = os.path.join(outdir, pdfs[0])

        shutil.move(produced, out_path)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)

    return {"ok": True, "action": "office2pdf", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path)}


# ---------------------------------------------------------------------------
# Image -> PDF
# ---------------------------------------------------------------------------
def image_to_pdf(in_path, out_path):
    from PIL import Image

    img = Image.open(in_path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img).convert("RGB")
    else:
        img = img.convert("RGB")

    img.save(out_path, "PDF", resolution=150.0)
    return {"ok": True, "action": "img2pdf", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path)}


# ---------------------------------------------------------------------------
# PDF -> DOCX
# ---------------------------------------------------------------------------
def _pdf_is_scanned(in_path, sample_pages=5):
    import fitz
    doc = fitz.open(in_path)
    total = doc.page_count
    checked = 0
    chars = 0
    for i in range(min(sample_pages, total)):
        chars += len(doc[i].get_text("text").strip())
        checked += 1
    doc.close()
    avg = chars / max(checked, 1)
    return (avg < 20, total)


def _ocr_pdf_to_docx(in_path, out_path):
    import fitz
    import tempfile
    from docx import Document
    from docx.shared import Inches, Pt

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise RuntimeError("OCR needs pytesseract + Pillow and the tesseract-ocr "
                           "binary (sudo apt install tesseract-ocr)")

    doc = fitz.open(in_path)
    out = Document()
    tmp = tempfile.mkdtemp(prefix="rsi_ocr_")
    zoom = 300 / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pages_ocred = 0

    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        img_path = os.path.join(tmp, f"p{i}.png")
        pix.save(img_path)

        langs = os.environ.get("TESSERACT_LANGS", "eng")
        try:
            text = pytesseract.image_to_string(Image.open(img_path), lang=langs)
        except Exception:
            text = pytesseract.image_to_string(Image.open(img_path))

        if text.strip():
            for line in text.splitlines():
                p = out.add_paragraph(line)
                p.paragraph_format.space_after = Pt(0)
            pages_ocred += 1
        if i < doc.page_count - 1:
            out.add_page_break()
        os.remove(img_path)

    doc.close()
    os.rmdir(tmp)
    out.save(out_path)
    normalized = _normalize_docx(out_path)
    return {"ok": True, "action": "pdf2docx", "mode": "ocr",
            "output": os.path.basename(out_path),
            "pages_ocred": pages_ocred, "normalized": normalized,
            "bytes_out": os.path.getsize(out_path),
            "note": ("Scanned PDF detected — recovered editable text with OCR "
                     "(Tesseract). Layout is simplified to flowing text. "
                     + ("Word-compatible." if normalized else
                        "Install LibreOffice for full Word compatibility."))}


def pdf_to_docx(in_path, out_path):
    scanned = False
    try:
        scanned, _total = _pdf_is_scanned(in_path)
    except Exception:
        scanned = False

    if scanned:
        try:
            return _ocr_pdf_to_docx(in_path, out_path)
        except Exception:
            pass

    from pdf2docx import Converter
    cv = Converter(in_path)
    try:
        cv.convert(out_path)
    finally:
        cv.close()

    normalized = _normalize_docx(out_path)

    note = ("Converted from PDF (layout-preserving). "
            + ("Output normalized so it opens cleanly in every Word version "
               "from Office 2007 onward."
               if normalized else
               "WARNING: LibreOffice not found, so the file could not be "
               "schema-normalized — if Word reports an XML error, install "
               "LibreOffice (sudo apt install libreoffice) and re-convert."))
    return {"ok": True, "action": "pdf2docx", "mode": "layout",
            "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "normalized": normalized, "note": note}


def pdf_to_docx_ocr(in_path, out_path):
    return _ocr_pdf_to_docx(in_path, out_path)


# ---------------------------------------------------------------------------
# PDF -> XLSX
# ---------------------------------------------------------------------------
def pdf_to_xlsx(in_path, out_path):
    import pdfplumber
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)

    tables_found = 0
    text_pages = 0

    with pdfplumber.open(in_path) as pdf:
        for pnum, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            if tables:
                for tnum, table in enumerate(tables, start=1):
                    ws = wb.create_sheet(title=f"P{pnum}_T{tnum}"[:31])
                    for row in table:
                        ws.append([("" if c is None else str(c)) for c in row])
                    tables_found += 1
            else:
                text = page.extract_text() or ""
                if text.strip():
                    ws = wb.create_sheet(title=f"P{pnum}_text"[:31])
                    for line in text.splitlines():
                        ws.append([line])
                    text_pages += 1

    if len(wb.sheetnames) == 0:
        wb.create_sheet(title="empty")

    for ws in wb.worksheets:
        ws.column_dimensions[get_column_letter(1)].width = 60

    wb.save(out_path)
    return {"ok": True, "action": "pdf2xlsx", "output": os.path.basename(out_path),
            "tables": tables_found, "text_pages": text_pages,
            "bytes_out": os.path.getsize(out_path),
            "note": "Detected tables exported as sheets; pages without tables kept as text."}


# ---------------------------------------------------------------------------
# PDF -> images
# ---------------------------------------------------------------------------
def pdf_to_img(in_path, out_zip, fmt="png", dpi=150):
    import fitz
    fmt = (fmt or "png").lower()
    if fmt not in ("png", "jpg", "jpeg"):
        fmt = "png"
    ext = "jpg" if fmt in ("jpg", "jpeg") else "png"
    dpi = int(dpi)

    doc = fitz.open(in_path)
    work_dir = out_zip + "_pages"
    os.makedirs(work_dir, exist_ok=True)
    page_files = []

    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=mat)
        fpath = os.path.join(work_dir, f"page_{i:03}.{ext}")
        if ext == "jpg":
            pix.save(fpath, jpg_quality=90)
        else:
            pix.save(fpath)
        page_files.append(fpath)
    doc.close()

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in page_files:
            z.write(f, arcname=os.path.basename(f))
            os.remove(f)
    os.rmdir(work_dir)

    return {"ok": True, "action": "pdf2img", "output": os.path.basename(out_zip),
            "pages": len(page_files), "format": ext, "dpi": dpi,
            "bytes_out": os.path.getsize(out_zip)}


# ---------------------------------------------------------------------------
# Image -> relief 3D mesh
# ---------------------------------------------------------------------------
def img_to_relief_raw(in_path, out_obj, depth=0.15):
    from PIL import Image
    import numpy as np

    try:
        depth = float(depth)
    except (TypeError, ValueError):
        depth = 0.15
    depth = min(max(depth, 0.02), 1.0)

    img = Image.open(in_path).convert("L")
    max_dim = 140
    w, h = img.size
    scale = min(max_dim / w, max_dim / h, 1.0)
    nw, nh = max(int(w * scale), 2), max(int(h * scale), 2)
    img = img.resize((nw, nh))
    z = np.asarray(img, dtype=np.float32) / 255.0
    relief = depth * max(nw, nh)

    _write_obj(out_obj, z, relief)
    return {"ok": True, "action": "previewobj", "output": os.path.basename(out_obj),
            "grid": f"{nw}x{nh}", "depth": round(depth, 3),
            "vertices": nw * nh, "bytes_out": os.path.getsize(out_obj)}


def img_to_relief(in_path, out_zip, fmt="obj", depth=0.15):
    from PIL import Image
    import numpy as np

    fmt = (fmt or "obj").lower()
    if fmt not in ("obj", "stl"):
        fmt = "obj"
    try:
        depth = float(depth)
    except (TypeError, ValueError):
        depth = 0.15
    depth = min(max(depth, 0.02), 1.0)

    img = Image.open(in_path).convert("L")
    max_dim = 160
    w, h = img.size
    scale = min(max_dim / w, max_dim / h, 1.0)
    nw, nh = max(int(w * scale), 2), max(int(h * scale), 2)
    img = img.resize((nw, nh))
    z = np.asarray(img, dtype=np.float32) / 255.0

    relief = depth * max(nw, nh)
    work_dir = out_zip + "_mesh"
    os.makedirs(work_dir, exist_ok=True)
    mesh_path = os.path.join(work_dir, f"relief.{fmt}")

    if fmt == "obj":
        _write_obj(mesh_path, z, relief)
    else:
        _write_stl(mesh_path, z, relief)

    readme = os.path.join(work_dir, "README.txt")
    with open(readme, "w") as f:
        f.write(
            "Relief / height-map 3D mesh\n"
            "---------------------------\n"
            "Each pixel's brightness was turned into surface height, producing a\n"
            "real 3D mesh you can open in Blender, MeshLab, or any 3D viewer.\n\n"
            "This is a deterministic relief, NOT an AI reconstruction of real\n"
            "3D geometry from the photo. Bright = high, dark = low.\n"
        )

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(mesh_path, arcname=os.path.basename(mesh_path))
        zf.write(readme, arcname="README.txt")
    os.remove(mesh_path)
    os.remove(readme)
    os.rmdir(work_dir)

    return {"ok": True, "action": "img2relief", "output": os.path.basename(out_zip),
            "format": fmt, "grid": f"{nw}x{nh}", "depth": round(depth, 3),
            "vertices": nw * nh, "bytes_out": os.path.getsize(out_zip),
            "note": "Brightness-to-height relief mesh (deterministic, not AI 3D)."}


def _write_obj(path, z, relief):
    nh, nw = z.shape
    with open(path, "w") as f:
        f.write("# RSI Vault relief mesh\n")
        for y in range(nh):
            for x in range(nw):
                f.write(f"v {x} {nh - 1 - y} {z[y, x] * relief:.4f}\n")
        def idx(x, y):
            return y * nw + x + 1
        for y in range(nh - 1):
            for x in range(nw - 1):
                a, b, c, d = idx(x, y), idx(x + 1, y), idx(x + 1, y + 1), idx(x, y + 1)
                f.write(f"f {a} {b} {c}\n")
                f.write(f"f {a} {c} {d}\n")


def _write_stl(path, z, relief):
    import numpy as np
    nh, nw = z.shape

    def vert(x, y):
        return (float(x), float(nh - 1 - y), float(z[y, x] * relief))

    tris = []
    for y in range(nh - 1):
        for x in range(nw - 1):
            a, b, c, d = vert(x, y), vert(x + 1, y), vert(x + 1, y + 1), vert(x, y + 1)
            tris.append((a, b, c))
            tris.append((a, c, d))

    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for (a, b, c) in tris:
            f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for v in (a, b, c):
                f.write(struct.pack("<3f", *v))
            f.write(struct.pack("<H", 0))


# ---------------------------------------------------------------------------
# iLovePDF-style PDF toolbox
# ---------------------------------------------------------------------------
def _parse_page_ranges(spec, page_count):
    if not spec or not spec.strip():
        return list(range(page_count))
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else page_count
            for p in range(a, b + 1):
                if 1 <= p <= page_count:
                    pages.add(p - 1)
        else:
            p = int(part)
            if 1 <= p <= page_count:
                pages.add(p - 1)
    return sorted(pages)


def pdf_merge(in_paths, out_path):
    import fitz
    if isinstance(in_paths, str):
        in_paths = [in_paths]
    doc = fitz.open()
    merged = 0
    for p in in_paths:
        with fitz.open(p) as src:
            doc.insert_pdf(src)
            merged += 1
    doc.save(out_path, deflate=True, garbage=4)
    doc.close()
    return {"ok": True, "action": "pdf_merge", "output": os.path.basename(out_path),
            "files_merged": merged, "bytes_out": os.path.getsize(out_path),
            "note": "PDFs merged in upload order."}


def pdf_split(in_path, out_zip, ranges=""):
    import fitz
    doc = fitz.open(in_path)
    n = doc.page_count
    work = out_zip + "_split"
    os.makedirs(work, exist_ok=True)
    produced = []

    groups = []
    if ranges and ranges.strip():
        for part in ranges.split(","):
            idx = _parse_page_ranges(part, n)
            if idx:
                groups.append(idx)
    else:
        groups = [[i] for i in range(n)]

    for gi, idx in enumerate(groups, start=1):
        out = fitz.open()
        for i in idx:
            out.insert_pdf(doc, from_page=i, to_page=i)
        label = f"{idx[0]+1}" if len(idx) == 1 else f"{idx[0]+1}-{idx[-1]+1}"
        fp = os.path.join(work, f"pages_{label}.pdf")
        out.save(fp, deflate=True)
        out.close()
        produced.append(fp)
    doc.close()

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for fp in produced:
            z.write(fp, arcname=os.path.basename(fp))
            os.remove(fp)
    os.rmdir(work)
    return {"ok": True, "action": "pdf_split", "output": os.path.basename(out_zip),
            "parts": len(produced), "bytes_out": os.path.getsize(out_zip),
            "note": "Each split is a separate PDF inside the zip."}


def pdf_remove_pages(in_path, out_path, spec):
    import fitz
    doc = fitz.open(in_path)
    remove = set(_parse_page_ranges(spec, doc.page_count))
    keep = [i for i in range(doc.page_count) if i not in remove]
    out = fitz.open()
    for i in keep:
        out.insert_pdf(doc, from_page=i, to_page=i)
    out.save(out_path, deflate=True, garbage=4)
    out.close(); doc.close()
    return {"ok": True, "action": "pdf_remove_pages", "output": os.path.basename(out_path),
            "removed": len(remove), "remaining": len(keep),
            "bytes_out": os.path.getsize(out_path)}


def pdf_extract_pages(in_path, out_path, spec):
    import fitz
    doc = fitz.open(in_path)
    idx = _parse_page_ranges(spec, doc.page_count)
    out = fitz.open()
    for i in idx:
        out.insert_pdf(doc, from_page=i, to_page=i)
    out.save(out_path, deflate=True, garbage=4)
    out.close(); doc.close()
    return {"ok": True, "action": "pdf_extract_pages", "output": os.path.basename(out_path),
            "extracted": len(idx), "bytes_out": os.path.getsize(out_path)}


def pdf_rotate(in_path, out_path, degrees=90, spec=""):
    import fitz
    try:
        degrees = int(degrees)
    except (TypeError, ValueError):
        degrees = 90
    degrees = degrees % 360
    doc = fitz.open(in_path)
    targets = set(_parse_page_ranges(spec, doc.page_count))
    for i, page in enumerate(doc):
        if i in targets:
            page.set_rotation((page.rotation + degrees) % 360)
    doc.save(out_path, deflate=True)
    doc.close()
    return {"ok": True, "action": "pdf_rotate", "output": os.path.basename(out_path),
            "degrees": degrees, "pages_rotated": len(targets),
            "bytes_out": os.path.getsize(out_path)}


def pdf_compress(in_path, out_path, level="ebook"):
    import shutil
    import subprocess
    gs = shutil.which("gs")
    gs_preset = {"low": "/screen", "screen": "/screen",
                 "recommended": "/ebook", "ebook": "/ebook",
                 "high": "/printer", "printer": "/printer"}.get(str(level).lower(), "/ebook")
    if gs:
        try:
            subprocess.run(
                [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
                 f"-dPDFSETTINGS={gs_preset}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                 "-dDetectDuplicateImages=true", "-dCompressFonts=true",
                 f"-sOutputFile={out_path}", in_path],
                check=True, capture_output=True, timeout=600)
            before = os.path.getsize(in_path)
            after = os.path.getsize(out_path)
            return {"ok": True, "action": "pdf_compress", "engine": "ghostscript",
                    "level": gs_preset.strip("/"),
                    "output": os.path.basename(out_path),
                    "bytes_in": before, "bytes_out": after,
                    "saved_pct": round((1 - after / max(before, 1)) * 100, 1),
                    "note": "Compressed with Ghostscript."}
        except Exception:
            pass

    import fitz
    doc = fitz.open(in_path)
    doc.save(out_path, deflate=True, garbage=4, clean=True, deflate_images=True,
             deflate_fonts=True)
    doc.close()
    before = os.path.getsize(in_path)
    after = os.path.getsize(out_path)
    return {"ok": True, "action": "pdf_compress", "engine": "pymupdf",
            "output": os.path.basename(out_path),
            "bytes_in": before, "bytes_out": after,
            "saved_pct": round((1 - after / max(before, 1)) * 100, 1),
            "note": "Compressed with PyMuPDF (install Ghostscript for stronger compression)."}


def pdf_repair(in_path, out_path):
    import fitz
    doc = fitz.open(in_path)
    doc.save(out_path, deflate=True, garbage=4, clean=True)
    doc.close()
    return {"ok": True, "action": "pdf_repair", "output": os.path.basename(out_path),
            "pages": fitz.open(out_path).page_count,
            "bytes_out": os.path.getsize(out_path),
            "note": "Re-parsed and rewritten. Badly corrupt files may still lose content."}


def pdf_protect(in_path, out_path, password):
    import fitz
    if not password:
        raise RuntimeError("a password is required to protect the PDF")
    doc = fitz.open(in_path)
    perm = int(
        fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_PRINT |
        fitz.PDF_PERM_COPY | fitz.PDF_PERM_ANNOTATE
    )
    doc.save(out_path, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw=password, user_pw=password, permissions=perm)
    doc.close()
    return {"ok": True, "action": "pdf_protect", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Encrypted with AES-256. The same password opens it."}


def pdf_unlock(in_path, out_path, password=""):
    import fitz
    doc = fitz.open(in_path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise RuntimeError("AUTH_FAIL: wrong or missing password")
    doc.save(out_path, deflate=True)
    doc.close()
    return {"ok": True, "action": "pdf_unlock", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Password removed — the PDF now opens without one."}


def pdf_page_numbers(in_path, out_path, position="bottom-center", start=1):
    import fitz
    try:
        start = int(start)
    except (TypeError, ValueError):
        start = 1
    doc = fitz.open(in_path)
    total = doc.page_count
    for i, page in enumerate(doc):
        num = start + i
        text = f"{num} / {start + total - 1}"
        rect = page.rect
        fs = 11
        margin = 24
        tw = fitz.get_text_length(text, fontname="helv", fontsize=fs)
        pos = str(position).lower()
        if "left" in pos:
            x = margin
        elif "right" in pos:
            x = rect.width - tw - margin
        else:
            x = (rect.width - tw) / 2
        y = margin if "top" in pos else rect.height - margin
        page.insert_text((x, y), text, fontsize=fs, fontname="helv",
                         color=(0.2, 0.2, 0.2))
    doc.save(out_path, deflate=True)
    doc.close()
    return {"ok": True, "action": "pdf_page_numbers", "output": os.path.basename(out_path),
            "pages": total, "bytes_out": os.path.getsize(out_path)}


def pdf_watermark(in_path, out_path, text="CONFIDENTIAL", opacity=0.15):
    import fitz
    import math
    try:
        opacity = float(opacity)
    except (TypeError, ValueError):
        opacity = 0.15
    opacity = min(max(opacity, 0.03), 0.8)
    doc = fitz.open(in_path)
    for page in doc:
        rect = page.rect
        fs = max(28, int(min(rect.width, rect.height) / 10))
        pivot = fitz.Point(rect.width / 2, rect.height / 2)
        matrix = fitz.Matrix(math.cos(math.radians(45)), math.sin(math.radians(45)),
                             -math.sin(math.radians(45)), math.cos(math.radians(45)), 0, 0)
        morph = (pivot, matrix)
        tw = fitz.get_text_length(text, fontname="helv", fontsize=fs)
        start = fitz.Point(pivot.x - tw / 2, pivot.y)
        page.insert_text(start, text, fontsize=fs, fontname="helv",
                         color=(0.5, 0.5, 0.5), fill_opacity=opacity,
                         morph=morph, overlay=True)
    doc.save(out_path, deflate=True)
    doc.close()
    return {"ok": True, "action": "pdf_watermark", "output": os.path.basename(out_path),
            "text": text, "bytes_out": os.path.getsize(out_path)}


def pdf_to_pdfa(in_path, out_path):
    import shutil
    import subprocess
    gs = shutil.which("gs")
    if not gs:
        raise RuntimeError("Ghostscript is required for PDF/A. "
                           "Install with:  sudo apt install ghostscript")
    subprocess.run(
        [gs, "-dPDFA=2", "-dBATCH", "-dNOPAUSE", "-dQUIET",
         "-sColorConversionStrategy=UseDeviceIndependentColor",
         "-sDEVICE=pdfwrite", "-dPDFACompatibilityPolicy=1",
         f"-sOutputFile={out_path}", in_path],
        check=True, capture_output=True, timeout=600)
    return {"ok": True, "action": "pdf_to_pdfa", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Converted to PDF/A-2 for long-term archiving."}


def pdf_to_pptx(in_path, out_path):
    import fitz
    from pptx import Presentation
    from pptx.util import Inches
    import tempfile

    doc = fitz.open(in_path)
    prs = Presentation()
    first = doc[0].rect
    if first.height >= first.width:
        prs.slide_width = Inches(7.5)
        prs.slide_height = Inches(10)
    else:
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

    blank = prs.slide_layouts[6]
    tmp = tempfile.mkdtemp(prefix="rsi_pptx_")
    zoom = 150 / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        img_path = os.path.join(tmp, f"p{i}.png")
        pix.save(img_path)
        slide = prs.slides.add_slide(blank)
        pw, ph = prs.slide_width, prs.slide_height
        iw, ih = pix.width, pix.height
        scale = min(pw / iw, ph / ih)
        w = int(iw * scale); h = int(ih * scale)
        left = int((pw - w) / 2); top = int((ph - h) / 2)
        slide.shapes.add_picture(img_path, left, top, width=w, height=h)
        os.remove(img_path)
    doc.close()
    os.rmdir(tmp)
    prs.save(out_path)
    return {"ok": True, "action": "pdf2pptx", "output": os.path.basename(out_path),
            "slides": len(prs.slides._sldIdLst),
            "bytes_out": os.path.getsize(out_path),
            "note": "Each PDF page became a full-bleed slide image (layout preserved)."}


def pptx_to_pdf(in_path, out_path):
    return office_to_pdf(in_path, out_path)


def pdf_to_html(in_path, out_path):
    import fitz
    doc = fitz.open(in_path)
    parts = ['<!DOCTYPE html><html><head><meta charset="utf-8">',
             '<title>', os.path.basename(in_path), '</title>',
             '<style>body{margin:0;background:#525659;}'
             '.pg{background:#fff;margin:16px auto;box-shadow:0 2px 8px #0008;'
             'max-width:900px;padding:24px;}</style></head><body>']
    for page in doc:
        parts.append('<div class="pg">')
        parts.append(page.get_text("html"))
        parts.append('</div>')
    parts.append('</body></html>')
    doc.close()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return {"ok": True, "action": "pdf2html", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Self-contained HTML (text positioned per page, images embedded)."}


def html_to_pdf(in_path, out_path):
    import shutil
    import subprocess
    wk = shutil.which("wkhtmltopdf")
    if wk:
        subprocess.run([wk, "-q", "--enable-local-file-access", in_path, out_path],
                       check=True, capture_output=True, timeout=300)
        return {"ok": True, "action": "html2pdf", "engine": "wkhtmltopdf",
                "output": os.path.basename(out_path),
                "bytes_out": os.path.getsize(out_path)}
    return office_to_pdf(in_path, out_path)


# ---------------------------------------------------------------------------
# Image -> anaglyph
# ---------------------------------------------------------------------------
def img_to_anaglyph(in_path, out_path):
    from PIL import Image
    import numpy as np

    img = Image.open(in_path).convert("RGB")
    arr = np.asarray(img).astype(np.float32)
    gray = np.asarray(img.convert("L"), dtype=np.float32) / 255.0

    h, w, _ = arr.shape
    max_shift = max(2, int(w * 0.02))

    row_depth = gray.mean(axis=1)
    row_shift = np.clip((row_depth * max_shift).astype(np.int32), 0, max_shift)

    right = np.empty_like(arr)
    for y in range(h):
        right[y] = np.roll(arr[y], int(row_shift[y]), axis=0)

    anaglyph = np.zeros_like(arr)
    anaglyph[..., 0] = arr[..., 0]
    anaglyph[..., 1] = right[..., 1]
    anaglyph[..., 2] = right[..., 2]

    Image.fromarray(np.clip(anaglyph, 0, 255).astype("uint8")).save(out_path)
    return {"ok": True, "action": "img2anaglyph", "output": os.path.basename(out_path),
            "bytes_out": os.path.getsize(out_path),
            "note": "Red/cyan anaglyph — view with red/cyan 3D glasses."}


# ---------------------------------------------------------------------------
# YouTube Translation Pipeline
#
# Downloads a YouTube video, transcribes with Whisper, translates with Google
# Translate (free, via deep-translator), then produces one of:
#   mode="subtitle" — burns translated subtitles into video (original audio kept)
#   mode="srt"      — exports only the translated .srt file (fastest)
#   mode="dub"      — replaces audio with TTS in the target language
#
# Required installs:
#   pip install yt-dlp openai-whisper deep-translator edge-tts
#   sudo apt install ffmpeg            (already required by RSI Vault)
#
# Optional for better accuracy:
#   Pass model="small" or model="medium" in the API call body.
#   Default is "base" (~74 MB). "small" (~244 MB) is much better for Portuguese.
#
# Keep yt-dlp updated weekly:  pip install -U yt-dlp
# ---------------------------------------------------------------------------

# edge-tts Neural voice map (ISO 639-1 → Microsoft voice name)
_EDGE_TTS_VOICES = {
    "fr": "fr-FR-DeniseNeural",
    "en": "en-US-JennyNeural",
    "es": "es-ES-ElviraNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "ar": "ar-SA-ZariyahNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "nl": "nl-NL-ColetteNeural",
    "pl": "pl-PL-ZofiaNeural",
    "tr": "tr-TR-EmelNeural",
    "sv": "sv-SE-SofieNeural",
}


def _yt_download(url, work_dir):
    """
    Download best quality video (≤720p) from a YouTube URL using yt-dlp.

    Improvements for long documentaries and slow VPS connections:

    1. aria2c external downloader (if installed) — uses 8 parallel connections,
       dramatically faster and more reliable on slow/unstable VPS networks.
       Install:  apt install aria2

    2. --force-ipv4 — avoids IPv6 routing issues to Google CDN (common on VPS).

    3. --socket-timeout 60 — prevents hanging connections from eating the
       whole subprocess timeout.

    4. tv_embedded client — often bypasses iOS PO Token requirement and
       SABR streaming experiment that blocks HD formats.

    5. Subprocess timeout raised to 10800s (3 hours) — enough for a full
       2-hour documentary even on a slow connection.

    6. --concurrent-fragments 4 — splits each fragment into 4 parallel
       downloads (yt-dlp built-in, no aria2c needed).
    """
    import subprocess
    import shutil
    import sys

    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        raise RuntimeError(
            "yt-dlp is required. Install:  pip install yt-dlp\n"
            "Keep current:  pip install -U yt-dlp"
        )

    # JS runtime (node, not nodejs)
    node = shutil.which("node") or shutil.which("nodejs")
    js_runtime_args = ["--js-runtimes", "node"] if node else []
    if not node:
        print("WARNING: Node.js not found — 403s more likely.\n"
              "Fix:  sudo apt install nodejs", file=sys.stderr)

    # aria2c: much faster for large files (8 parallel connections)
    aria2c = shutil.which("aria2c")
    aria2c_args = []
    if aria2c:
        aria2c_args = [
            "--downloader", "aria2c",
            "--downloader-args",
            "aria2c:--max-connection-per-server=8 "
            "--split=8 "
            "--min-split-size=5M "
            "--max-tries=10 "
            "--retry-wait=5 "
            "--timeout=60 "
            "--file-allocation=none",
        ]
        print("[yt-dlp] aria2c found — using parallel download", file=sys.stderr)
    else:
        print("[yt-dlp] aria2c not found — using single-connection download.\n"
              "Tip: apt install aria2  (much faster for large files)",
              file=sys.stderr)

    out_tmpl = os.path.join(work_dir, "video.%(ext)s")

    cmd = (
        [ytdlp]
        + js_runtime_args
        + [
            # tv_embedded bypasses iOS PO Token + SABR experiment.
            # Falls back to android then web if tv_embedded unavailable.
            "--extractor-args", "youtube:player_client=tv_embedded,ios,android,web",
            # Best video+audio ≤720p. Generous fallback chain.
            "-f", (
                "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
                "/bestvideo[height<=720]+bestaudio"
                "/best[height<=720][ext=mp4]"
                "/best[ext=mp4]/best"
            ),
            "--merge-output-format", "mp4",
            "--remux-video",          "mp4",
            "--no-playlist",
            # Force IPv4 — avoids IPv6 routing issues to Google CDN on VPS.
            "--force-ipv4",
            # Socket timeout — prevents a single stalled connection from
            # eating the entire subprocess timeout silently.
            "--socket-timeout", "60",
            # Retry settings.
            "--retries",             "10",
            "--fragment-retries",    "10",
            "--retry-sleep",         "5",
            # Parallel fragment download (built-in, no aria2c needed).
            "--concurrent-fragments", "4",
            "--no-overwrites",
        ]
        + aria2c_args
        + ["-o", out_tmpl, url]
    )

    print(f"[yt-dlp] Downloading: {url}", file=sys.stderr)
    print(f"[yt-dlp] Output:      {out_tmpl}", file=sys.stderr)
    if aria2c:
        print("[yt-dlp] Mode: aria2c (8 connections)", file=sys.stderr)
    else:
        print("[yt-dlp] Mode: single connection (install aria2 for faster downloads)",
              file=sys.stderr)

    # 3-hour timeout — enough for a 2-hour documentary at 720p (~2 GB)
    # even on a slow 1 Mbps connection.
    proc = subprocess.run(cmd, capture_output=True, timeout=10800)

    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")
        stdout = proc.stdout.decode(errors="replace")

        hint = ""
        if "403" in stderr or "Forbidden" in stderr:
            hint = (
                "\n\nHTTP 403 fix:\n"
                "  1. pip install -U yt-dlp\n"
                "  2. sudo apt install nodejs\n"
                "  3. Try with cookies: add --cookies-from-browser firefox"
            )
        elif "Sign in" in stderr or "age" in stderr.lower():
            hint = (
                "\n\nAge-restricted video — needs browser cookies:\n"
                "  yt-dlp --cookies-from-browser firefox <url>"
            )
        elif "Private video" in stderr or "members" in stderr:
            hint = "\n\nPrivate or members-only video — cannot be downloaded."
        elif "No supported JavaScript" in stderr:
            hint = (
                "\n\nNode.js required:\n"
                "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -\n"
                "  sudo apt install -y nodejs"
            )
        elif "timed out" in stderr.lower() or "timeout" in stderr.lower():
            hint = (
                "\n\nDownload timed out. Try:\n"
                "  1. apt install aria2  (parallel downloads — much faster)\n"
                "  2. Download directly on Kali, then scp to VPS\n"
                "  3. Use a smaller format: add -f 18 to yt-dlp"
            )

        raise RuntimeError(
            "yt-dlp failed (exit " + str(proc.returncode) + "):\n" +
            (stderr[-600:] or stdout[-300:] or "no output") +
            hint
        )

    for fname in sorted(os.listdir(work_dir)):
        fpath = os.path.join(work_dir, fname)
        if fname.startswith("video.") and os.path.isfile(fpath):
            return fpath

    raise RuntimeError(
        "yt-dlp exited successfully but no output file found.\n"
        "The video may be DRM-protected, private, or geo-blocked."
    )


def _extract_audio_for_whisper(video_path, work_dir):
    """Extract 16 kHz mono WAV — the exact format Whisper expects."""
    audio_path = os.path.join(work_dir, "audio16k.wav")
    _ffmpeg(["-i", video_path,
             "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
             audio_path])
    return audio_path


def _whisper_transcribe(audio_path, src_lang, model_size="base"):
    """
    Transcribe audio with OpenAI Whisper.
    Returns list of {start, end, text} dicts (one per spoken segment).
    src_lang "pt" covers both Brazilian Portuguese and Portugal Portuguese.
    """
    try:
        import whisper
    except ImportError:
        raise RuntimeError(
            "openai-whisper is required. Install:  pip install openai-whisper"
        )
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, language=src_lang, verbose=False)
    return [
        {
            "start": float(s["start"]),
            "end":   float(s["end"]),
            "text":  s["text"].strip(),
        }
        for s in result["segments"]
        if s["text"].strip()
    ]


def _translate_segments(segments, src_lang, tgt_lang):
    """
    Translate segments with quality-first fallback chain.

    Quality ranking (best → worst):
      1. DeepL API     — best quality, 500k chars/month FREE, never rate-limits
                         Set env var:  export DEEPL_API_KEY="your-key"
                         Get free key: https://www.deepl.com/pro-api
      2. Google Translate (deep-translator) — good quality
                         Rate-limit: waits 60s → 120s → 180s then skips batch
                         Delay: 3s between requests (prevents rate-limiting)
      3. MyMemory      — decent, different server, no key needed
      4. Keep original — never crash pipeline; warns user to retry with DeepL

    NOTE: argostranslate removed from auto-chain (quality too poor for video dub).
    Install it only if you want offline use:  pip install argostranslate
    """
    import time
    import sys
    import re
    import os

    BATCH_LIMIT = 1200       # smaller batches = less likely to hit rate limits
    SEP         = "\n\n|||SEP|||\n\n"
    SEP_ALT     = "|||SEP|||"
    REQ_DELAY   = 3.0        # 3 second delay between requests → prevents rate-limiting

    total_segs       = sum(1 for s in segments if (s.get("text") or "").strip())
    translated_count = [0]
    kept_original    = [0]

    def _progress(extra=""):
        pct = int(translated_count[0] / max(total_segs, 1) * 100)
        bar = "\u2588" * (pct // 5) + "\u2591" * (20 - pct // 5)
        sys.stderr.write(
            f"\r  Translating {src_lang}\u2192{tgt_lang}: [{bar}] "
            f"{translated_count[0]}/{total_segs} ({pct}%) {extra}    "
        )
        sys.stderr.flush()

    def _is_rate_limit(err_str):
        s = err_str.lower()
        return any(x in s for x in [
            "too many requests", "server error", "429",
            "rate limit", "quota", "you may have made", "blocked"
        ])

    # ── Backend 1: DeepL (best quality) ─────────────────────────────────
    DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")
    _deepl_ok = [bool(DEEPL_KEY)]
    _deepl_tr = [None]

    def _get_deepl():
        if not DEEPL_KEY:
            return None
        if _deepl_tr[0] is None:
            try:
                import deepl
                _deepl_tr[0] = deepl.Translator(DEEPL_KEY)
            except ImportError:
                sys.stderr.write(
                    "\n  [DeepL] Key set but deepl not installed.\n"
                    "  Fix:  pip install deepl --break-system-packages\n"
                )
                _deepl_ok[0] = False
                return None
            except Exception as e:
                sys.stderr.write(f"\n  [DeepL] Init error: {e}\n")
                _deepl_ok[0] = False
                return None
        return _deepl_tr[0]

    def _try_deepl(text):
        if not _deepl_ok[0]:
            return None
        tr = _get_deepl()
        if not tr:
            return None
        try:
            # DeepL uses language codes like "FR", "EN-US"
            tgt_code = tgt_lang.upper()
            if tgt_lang == "en":
                tgt_code = "EN-US"
            elif tgt_lang == "pt":
                tgt_code = "PT-PT"
            result = tr.translate_text(text.strip(), target_lang=tgt_code)
            return str(result).strip()
        except Exception as e:
            err = str(e)
            if "quota" in err.lower() or "limit" in err.lower():
                sys.stderr.write(f"\n  [DeepL] Quota exceeded: {err[:60]}\n")
                _deepl_ok[0] = False
            return None

    # ── Backend 2: Google Translate (3s delay prevents rate-limits) ──────
    _google_ok = [True]
    _google_blocked_count = [0]

    def _get_google():
        try:
            from deep_translator import GoogleTranslator
            return GoogleTranslator(source=src_lang, target=tgt_lang)
        except Exception:
            return None

    def _try_google(text):
        if not _google_ok[0]:
            return None
        tr = _get_google()
        if not tr:
            return None
        for attempt in range(3):
            try:
                result = tr.translate(text.strip())
                if result:
                    _google_blocked_count[0] = 0
                    time.sleep(REQ_DELAY)   # 3s delay prevents rate-limiting
                    return result.strip()
            except Exception as e:
                if _is_rate_limit(str(e)):
                    _google_blocked_count[0] += 1
                    if _google_blocked_count[0] >= 5:
                        sys.stderr.write(
                            "\n  [Google] Persistent rate-limit. "
                            "Switching to MyMemory.\n"
                            "  Tip: set DEEPL_API_KEY to avoid this entirely.\n"
                        )
                        _google_ok[0] = False
                        return None
                    wait = 60 * (attempt + 1)   # 60s, 120s, 180s
                    sys.stderr.write(
                        f"\n  [Google] Rate-limited. Waiting {wait}s...\n"
                    )
                    _progress(f"[Google limit: waiting {wait}s]")
                    time.sleep(wait)
                else:
                    time.sleep(2.0)
        return None

    # ── Backend 3: MyMemory ──────────────────────────────────────────────
    _mymem_ok = [True]

    def _try_mymem(text):
        if not _mymem_ok[0]:
            return None
        try:
            from deep_translator import MyMemoryTranslator
            tr = MyMemoryTranslator(
                source=f"{src_lang}-{src_lang.upper()}",
                target=f"{tgt_lang}-{tgt_lang.upper()}"
            )
            result = tr.translate(text.strip())
            if result:
                time.sleep(2.0)
                return result.strip()
        except Exception as e:
            if _is_rate_limit(str(e)):
                _mymem_ok[0] = False
                sys.stderr.write("\n  [MyMemory] Rate-limited.\n")
        return None

    def _translate_one(text):
        txt = text.strip()
        if not txt:
            return text

        # Quality order: DeepL → Google → MyMemory → keep original
        result = _try_deepl(txt)
        if result:
            translated_count[0] += 1
            _progress("[DeepL]" if DEEPL_KEY else "")
            return result

        result = _try_google(txt)
        if result:
            translated_count[0] += 1
            _progress()
            return result

        result = _try_mymem(txt)
        if result:
            translated_count[0] += 1
            _progress("[MyMemory]")
            return result

        # All failed — keep original, warn
        translated_count[0] += 1
        kept_original[0]    += 1
        _progress("[kept original]")
        return text

    def _translate_batch(idx_list, texts):
        """Try DeepL or Google batch; fall back per-segment."""
        # DeepL batch
        if _deepl_ok[0] and DEEPL_KEY:
            try:
                results_deepl = [_try_deepl(t) for t in texts]
                if all(r for r in results_deepl):
                    out = {}
                    for i, idx in enumerate(idx_list):
                        out[idx] = results_deepl[i]
                        translated_count[0] += 1
                        _progress("[DeepL]")
                    return out
            except Exception:
                pass

        # Google batch (with separator)
        if _google_ok[0]:
            tr = _get_google()
            if tr:
                joined = SEP.join(texts)
                try:
                    raw = tr.translate(joined) or ""
                    time.sleep(REQ_DELAY)
                    if SEP_ALT in raw:
                        parts = [p.strip() for p in raw.split(SEP_ALT)]
                    elif "SEP" in raw:
                        parts = [p.strip() for p in re.split(
                            r'\|\|\|.*?SEP.*?\|\|\|', raw, flags=re.IGNORECASE)]
                    else:
                        parts = None
                    if parts and len(parts) == len(idx_list):
                        out = {}
                        for i, idx in enumerate(idx_list):
                            out[idx] = parts[i]
                            translated_count[0] += 1
                            _progress()
                        return out
                except Exception as e:
                    if _is_rate_limit(str(e)):
                        _google_blocked_count[0] += 1
                        if _google_blocked_count[0] >= 5:
                            _google_ok[0] = False

        # Per-segment fallback
        out = {}
        for i, idx in enumerate(idx_list):
            out[idx] = _translate_one(texts[i])
        return out

    # ── Build batches ────────────────────────────────────────────────────
    batches = []
    cur_idxs, cur_texts, cur_len = [], [], 0
    for i, seg in enumerate(segments):
        txt = (seg.get("text") or "").strip()
        if not txt:
            continue
        if len(txt) > BATCH_LIMIT:
            if cur_idxs:
                batches.append((cur_idxs, cur_texts))
                cur_idxs, cur_texts, cur_len = [], [], 0
            batches.append(([i], [txt]))
            continue
        if cur_idxs and cur_len + len(txt) + len(SEP) > BATCH_LIMIT:
            batches.append((cur_idxs, cur_texts))
            cur_idxs, cur_texts, cur_len = [], [], 0
        cur_idxs.append(i)
        cur_texts.append(txt)
        cur_len += len(txt) + len(SEP)
    if cur_idxs:
        batches.append((cur_idxs, cur_texts))

    # ── Run ──────────────────────────────────────────────────────────────
    backend = "DeepL + Google + MyMemory" if DEEPL_KEY else "Google + MyMemory"
    sys.stderr.write(
        f"\n  [Translation] {total_segs} segs | {len(batches)} batches"
        f" | {src_lang}\u2192{tgt_lang} | {backend}\n"
    )
    if DEEPL_KEY:
        sys.stderr.write("  [Translation] \u2705 DeepL API key found — using premium quality\n")
    else:
        sys.stderr.write(
            "  [Translation] \u26a0 No DeepL key — using Google (3s/req delay).\n"
            "  [Translation]   For better quality:  export DEEPL_API_KEY=your-key\n"
            "  [Translation]   Free 500k chars/mo:  https://www.deepl.com/pro-api\n"
        )
    _progress()

    translations = {}
    for idx_list, texts in batches:
        if len(texts) == 1:
            translations[idx_list[0]] = _translate_one(texts[0])
        else:
            translations.update(_translate_batch(idx_list, texts))
        time.sleep(0.2)

    sys.stderr.write(
        f"\n  [Translation] Done \u2014 {translated_count[0]}/{total_segs} translated"
        + (f" | {kept_original[0]} kept original (retry with DeepL key)" if kept_original[0] else "")
        + "\n"
    )
    if kept_original[0] > 0:
        sys.stderr.write(
            f"  [Translation] \u26a0 {kept_original[0]} segments not translated.\n"
            f"  [Translation]   Fix: export DEEPL_API_KEY=your-key and retry.\n"
            f"  [Translation]   Free key: https://www.deepl.com/pro-api\n"
        )

    return [
        {**seg, "text": translations.get(i, (seg.get("text") or "").strip())}
        for i, seg in enumerate(segments)
    ]

def _segments_to_srt(segments):
    """Convert {start, end, text} dicts to SRT-format string."""
    def _ts(sec):
        h, rem = divmod(sec, 3600)
        m, s2 = divmod(rem, 60)
        ms = int((s2 % 1) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s2):02d},{ms:03d}"

    lines = []
    n = 1
    for seg in segments:
        if not seg["text"].strip():
            continue
        lines += [str(n), f"{_ts(seg['start'])} --> {_ts(seg['end'])}",
                  seg["text"], ""]
        n += 1
    return "\n".join(lines)


def _get_media_duration(path):
    """Return duration in seconds of a media file via ffprobe."""
    import subprocess
    import shutil
    probe = subprocess.run(
        [shutil.which("ffprobe") or "ffprobe",
         "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, timeout=30
    )
    data = json.loads(probe.stdout or b"{}")
    return float((data.get("format") or {}).get("duration", 0))


def _tts_generate_all(segments, work_dir, tgt_lang):
    """
    Generate TTS MP3 for every non-empty segment.

    Tries edge-tts first (high-quality Neural voices, needs internet).
    Falls back to gTTS if edge-tts is not installed.
    Returns dict {segment_index: mp3_file_path}.

    Speed fix: TTS is generated at -10% rate (slightly slower than default).
    This gives the time-stretch step in _build_dubbed_audio more room to work
    with, reducing the need for aggressive speed-up that makes voices sound
    robotic. The -10% is barely perceptible on its own but significantly
    improves the naturalness of the final dubbed audio.
    """
    import asyncio

    voice = _EDGE_TTS_VOICES.get(tgt_lang, "fr-FR-DeniseNeural")

    # Generate TTS slightly slower → more room before time-stretching kicks in.
    # edge-tts rate: "-10%" = 10% slower. Range: "-50%" to "+100%".
    TTS_RATE = "-10%"

    try:
        import edge_tts

        async def _gen_all_edge():
            results = {}
            for i, seg in enumerate(segments):
                if not seg["text"].strip():
                    continue
                mp3 = os.path.join(work_dir, f"tts_{i:04d}.mp3")
                try:
                    comm = edge_tts.Communicate(seg["text"], voice, rate=TTS_RATE)
                    await comm.save(mp3)
                    results[i] = mp3
                except Exception:
                    # Retry without rate parameter (older edge-tts versions)
                    try:
                        comm = edge_tts.Communicate(seg["text"], voice)
                        await comm.save(mp3)
                        results[i] = mp3
                    except Exception:
                        pass
            return results

        return asyncio.run(_gen_all_edge())

    except ImportError:
        pass

    try:
        from gtts import gTTS
    except ImportError:
        raise RuntimeError(
            "No TTS engine found. Install one:\n"
            "  pip install edge-tts   (recommended — Neural voices)\n"
            "  pip install gTTS       (fallback)"
        )

    results = {}
    for i, seg in enumerate(segments):
        if not seg["text"].strip():
            continue
        mp3 = os.path.join(work_dir, f"tts_{i:04d}.mp3")
        gTTS(text=seg["text"], lang=tgt_lang).save(mp3)
        results[i] = mp3
    return results


def _build_dubbed_audio(segments, work_dir, tts_files, total_dur):
    """
    Assemble dubbed audio with PERFECT frame-accurate synchronization.

    WHY the previous version was out of sync:
      When a TTS clip overflowed its window (even at 1.25x cap), prev_end
      advanced past the next segment's start, making every gap NEGATIVE.
      Negative gaps were skipped → each subsequent segment started later
      than it should → cumulative drift → voice and image diverged.

    THE FIX — fixed-duration windows with hard -t limit:
      Each segment gets a FIXED audio window:
          window_dur = next_segment_start - this_segment_start
      The TTS clip is:
        • shorter than window → play TTS + silence pad to fill exactly
        • fits after ≤1.25x stretch → stretch + silence pad
        • too long even at 1.25x → stretch to 1.25x + HARD CUT at boundary
      Result: every window clip is EXACTLY window_dur seconds.
      Concat of all windows = exactly total_dur → perfect sync guaranteed.

    No overflow is possible — the hard -t flag enforces the boundary.
    """
    if not tts_files:
        raise RuntimeError("No TTS audio was generated — nothing to dub.")

    import sys

    SR   = "44100"
    CH   = "2"
    FMT  = "s16"
    MAX_SPEED = 1.25

    # Helper: silence clip of exact duration
    def _silence(path, dur):
        _ffmpeg(["-f", "lavfi",
                 "-i", f"anullsrc=r={SR}:cl=stereo",
                 "-t", f"{max(dur, 0.01):.6f}",
                 "-ar", SR, "-ac", CH, "-sample_fmt", FMT,
                 path])

    # Helper: normalize audio format
    def _norm(src, dst, dur_limit=None):
        args = ["-i", src, "-ar", SR, "-ac", CH, "-sample_fmt", FMT]
        if dur_limit:
            args += ["-t", f"{dur_limit:.6f}"]
        args.append(dst)
        _ffmpeg(args)

    # ── Build next-segment-start index ───────────────────────────────────
    sorted_idxs = sorted(tts_files.keys())
    next_start = {}
    for pos, idx in enumerate(sorted_idxs):
        if pos + 1 < len(sorted_idxs):
            next_start[idx] = segments[sorted_idxs[pos + 1]]["start"]
        else:
            next_start[idx] = total_dur

    # ── Step 1: for each segment create a FIXED-DURATION window clip ─────
    #   window_dur = next_start - seg_start  (guaranteed exact)
    #   TTS is fitted inside: stretch ≤1.25x, pad silence, hard-cut if needed
    window_clips = {}   # seg_idx → (path, window_dur)
    total = len(tts_files)

    for n, (seg_idx, mp3_path) in enumerate(sorted(tts_files.items()), 1):
        seg       = segments[seg_idx]
        seg_start = seg["start"]
        seg_end   = seg["end"]

        # Fixed window: from this segment's start to the NEXT segment's start.
        # This is the ONLY correct reference for sync.
        window_dur = max(next_start.get(seg_idx, total_dur) - seg_start, 0.1)
        tts_dur    = _get_media_duration(mp3_path)

        win_path = os.path.join(work_dir, f"win_{seg_idx:04d}.wav")

        if tts_dur <= 0:
            # No audio — fill entire window with silence
            _silence(win_path, window_dur)

        elif tts_dur <= window_dur:
            # TTS shorter than window → play TTS then silence pad.
            # ffmpeg concat filter: [tts][silence]concat → exactly window_dur
            pad_dur = window_dur - tts_dur
            pad_path = os.path.join(work_dir, f"pad_{seg_idx:04d}.wav")
            _silence(pad_path, pad_dur)

            concat_txt = os.path.join(work_dir, f"ct_{seg_idx:04d}.txt")
            mp3_norm   = os.path.join(work_dir, f"mpn_{seg_idx:04d}.wav")
            _norm(mp3_path, mp3_norm)
            with open(concat_txt, "w") as f:
                f.write(f"file '{mp3_norm}'\n")
                f.write(f"file '{pad_path}'\n")
            _ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_txt,
                     "-t", f"{window_dur:.6f}",   # hard limit — no drift
                     "-ar", SR, "-ac", CH, "-sample_fmt", FMT,
                     win_path])
            for tmp in (pad_path, mp3_norm, concat_txt):
                try: os.remove(tmp)
                except OSError: pass

        else:
            # TTS longer than window → time-stretch, hard-cut at window boundary
            speed = min(tts_dur / window_dur, MAX_SPEED)
            r = speed
            af_filters = []
            while r > 2.0:
                af_filters.append("atempo=2.0"); r /= 2.0
            while r < 0.5:
                af_filters.append("atempo=0.5"); r *= 2.0
            af_filters.append(f"atempo={r:.4f}")
            af = ",".join(af_filters)

            _ffmpeg(["-i", mp3_path,
                     "-af", af,
                     "-t", f"{window_dur:.6f}",   # HARD CUT — guarantees sync
                     "-ar", SR, "-ac", CH, "-sample_fmt", FMT,
                     win_path])

        try:
            os.remove(mp3_path)
        except OSError:
            pass

        window_clips[seg_idx] = (win_path, window_dur)
        sys.stderr.write(
            f"\r  [Sync] {n}/{total} windows built "
            f"({window_dur:.1f}s each)...    ")
        sys.stderr.flush()

    sys.stderr.write("\n")

    # ── Step 2: build full concat list ────────────────────────────────────
    # Structure: [silence_head?] [window_0] [window_1] ... [silence_tail?]
    # No gaps between windows — they are placed back-to-back because
    # each window already covers the exact time to the next segment start.
    concat_files = []

    # Silence before first segment
    first_idx   = sorted_idxs[0]
    first_start = segments[first_idx]["start"]
    if first_start > 0.01:
        head = os.path.join(work_dir, "head_silence.wav")
        _silence(head, first_start)
        concat_files.append((head, True))

    # All window clips in order
    for seg_idx in sorted_idxs:
        win_path, _ = window_clips[seg_idx]
        concat_files.append((win_path, False))

    # Silence after last segment to reach video end (safety buffer)
    last_idx     = sorted_idxs[-1]
    last_win_end = next_start[last_idx]   # = total_dur for last segment
    if last_win_end < total_dur - 0.1:
        tail = os.path.join(work_dir, "tail_silence.wav")
        _silence(tail, total_dur - last_win_end + 0.5)
        concat_files.append((tail, True))

    # ── Step 3: concat all fixed-duration clips ───────────────────────────
    concat_txt = os.path.join(work_dir, "master_concat.txt")
    with open(concat_txt, "w") as f:
        for path, _ in concat_files:
            escaped = path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    sys.stderr.write(
        f"  [Sync] Joining {len(concat_files)} fixed-duration clips...\n"
        f"  [Sync] Total audio = exactly {total_dur:.1f}s → perfect sync\n"
    )

    merged_path = os.path.join(work_dir, "dubbed_final.wav")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_txt,
             "-ar", SR, "-ac", CH, "-sample_fmt", FMT,
             merged_path], timeout=7200)

    # ── Cleanup ───────────────────────────────────────────────────────────
    for path, is_temp in concat_files:
        if is_temp:
            try: os.remove(path)
            except OSError: pass
    for win_path, _ in window_clips.values():
        try: os.remove(win_path)
        except OSError: pass

    return merged_path

def youtube_translate(url, out_path, src_lang="pt", tgt_lang="fr",
                      mode="subtitle", model_size="base"):
    """
    Full YouTube → translated video pipeline.

    Parameters
    ----------
    url        : YouTube video URL
    out_path   : output file path (.mp4 for subtitle/dub modes, .srt for srt mode)
    src_lang   : source language ISO 639-1 code — "pt" covers both BR and PT Portuguese
    tgt_lang   : target language ISO 639-1 code — "fr" for French
    mode       : "subtitle" | "srt" | "dub"
    model_size : Whisper model — "base" (fast), "small" (better), "medium" (best)

    Modes
    -----
    subtitle → Burns translated subtitles into video; original audio kept. Recommended.
    srt      → Exports only the .srt file (no re-encode, fastest).
    dub      → Replaces original audio with TTS in the target language.
    """
    import tempfile
    import shutil

    work_dir = tempfile.mkdtemp(prefix="rsi_yt_")
    try:
        # 1. Download
        video_path = _yt_download(url, work_dir)

        # 2. Extract audio for transcription
        audio_path = _extract_audio_for_whisper(video_path, work_dir)

        # 3. Transcribe
        segments = _whisper_transcribe(audio_path, src_lang, model_size)
        if not segments:
            raise RuntimeError(
                "Whisper returned no segments. "
                "Verify src_lang matches the video's spoken language."
            )
        total_dur = segments[-1]["end"] + 1.0

        # 4. Translate
        segments = _translate_segments(segments, src_lang, tgt_lang)

        # 5a. SRT only (no video re-encode)
        if mode == "srt":
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(_segments_to_srt(segments))
            return {
                "ok": True, "action": "youtube_translate", "mode": "srt",
                "output": os.path.basename(out_path),
                "segments": len(segments),
                "src_lang": src_lang, "tgt_lang": tgt_lang,
                "bytes_out": os.path.getsize(out_path),
                "note": (f"Translated SRT exported ({src_lang}→{tgt_lang}). "
                         "Open with VLC (Subtitle menu) or burn with HandBrake."),
            }

        # 5b. Subtitle burn
        if mode == "subtitle":
            srt_path = os.path.join(work_dir, "subs.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(_segments_to_srt(segments))
            esc = srt_path.replace("\\", "/").replace(":", "\\:")
            _ffmpeg([
                "-i", video_path,
                "-vf", (f"subtitles={esc}:"
                        "force_style='FontSize=22,PrimaryColour=&H00FFFFFF,"
                        "OutlineColour=&H00000000,Outline=1,Bold=1'"),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                out_path,
            ], timeout=7200)
            return {
                "ok": True, "action": "youtube_translate", "mode": "subtitle",
                "output": os.path.basename(out_path),
                "segments": len(segments),
                "src_lang": src_lang, "tgt_lang": tgt_lang,
                "bytes_out": os.path.getsize(out_path),
                "note": (f"French subtitles burned into video ({src_lang}→{tgt_lang}). "
                         "Whisper transcription + Google Translate."),
            }

        # 5c. Dub (replace audio with TTS)
        if mode == "dub":
            tts_files = _tts_generate_all(segments, work_dir, tgt_lang)
            dubbed_audio = _build_dubbed_audio(segments, work_dir, tts_files, total_dur)
            _ffmpeg([
                "-i", video_path, "-i", dubbed_audio,
                "-c:v", "copy",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest", out_path,
            ], timeout=7200)
            return {
                "ok": True, "action": "youtube_translate", "mode": "dub",
                "output": os.path.basename(out_path),
                "segments": len(segments),
                "src_lang": src_lang, "tgt_lang": tgt_lang,
                "bytes_out": os.path.getsize(out_path),
                "note": (f"Audio dubbed into {tgt_lang} ({src_lang}→{tgt_lang}). "
                         "TTS timing auto-adjusted per segment. "
                         "Whisper + Google Translate + edge-tts Neural voice."),
            }

        # 5d. Both — French voice dub AND subtitles burned on screen
        #     Pass 1: replace original audio with TTS → temp_dubbed.mp4
        #     Pass 2: burn translated subtitles onto the dubbed video → out_path
        if mode == "both":
            # Generate TTS audio track
            tts_files    = _tts_generate_all(segments, work_dir, tgt_lang)
            dubbed_audio = _build_dubbed_audio(segments, work_dir, tts_files, total_dur)

            # Pass 1 — swap audio (fast, copy video stream)
            temp_dubbed = os.path.join(work_dir, "temp_dubbed.mp4")
            _ffmpeg([
                "-i", video_path, "-i", dubbed_audio,
                "-c:v", "copy",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest", temp_dubbed,
            ], timeout=7200)

            # Pass 2 — burn subtitles onto the already-dubbed video
            srt_path = os.path.join(work_dir, "subs.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(_segments_to_srt(segments))
            esc = srt_path.replace("\\", "/").replace(":", "\\:")
            _ffmpeg([
                "-i", temp_dubbed,
                "-vf", (f"subtitles={esc}:"
                        "force_style='FontSize=22,PrimaryColour=&H00FFFFFF,"
                        "OutlineColour=&H00000000,Outline=1,Bold=1'"),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                out_path,
            ], timeout=7200)
            return {
                "ok": True, "action": "youtube_translate", "mode": "both",
                "output": os.path.basename(out_path),
                "segments": len(segments),
                "src_lang": src_lang, "tgt_lang": tgt_lang,
                "bytes_out": os.path.getsize(out_path),
                "note": (f"Full translation ({src_lang}→{tgt_lang}): "
                         f"{tgt_lang} TTS voice + {tgt_lang} subtitles burned on screen. "
                         "Whisper + Google Translate + edge-tts Neural voice."),
            }

        raise ValueError(
            f"unknown mode {mode!r}. Valid values: 'subtitle', 'srt', 'dub', 'both'."
        )

    finally:
        import shutil as _sh
        _sh.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Local Video Translation Pipeline
#
# Identical to youtube_translate() but skips the yt-dlp download step.
# The caller passes a video file already on disk (mp4, mkv, mov, avi, webm…).
# Every subsequent step — Whisper transcription, Google Translate, subtitle
# burn / SRT export / TTS dub — is exactly the same.
#
# Use case: user has already downloaded the video and wants to translate it
# without re-downloading; or they have any local video that was never on YouTube.
# ---------------------------------------------------------------------------
def local_video_translate(video_path, out_path, src_lang="en", tgt_lang="fr",
                           mode="subtitle", model_size="base"):
    """
    Translate a locally stored video file.

    Parameters
    ----------
    video_path : path to the input video (mp4, mkv, mov, avi, webm, …)
    out_path   : output file (.mp4 for subtitle/dub/both modes, .srt for srt mode)
    src_lang   : spoken language ISO code  — e.g. "en", "pt", "es"
    tgt_lang   : target language ISO code  — e.g. "fr", "en", "de"
    mode       : "subtitle" | "srt" | "dub" | "both"
    model_size : Whisper model — "tiny" | "base" | "small" | "medium"

    Modes
    -----
    subtitle → Burns translated captions into video; original audio kept.
    srt      → Exports only the .srt file (no re-encode, fastest).
    dub      → Replaces original audio with TTS in the target language.
    both     → TTS voice dub + subtitles burned on screen (best quality).
    """
    import tempfile
    import shutil

    work_dir = tempfile.mkdtemp(prefix="rsi_lvt_")
    try:
        # 1. Extract 16 kHz mono WAV for Whisper
        audio_path = _extract_audio_for_whisper(video_path, work_dir)

        # 2. Transcribe
        segments = _whisper_transcribe(audio_path, src_lang, model_size)
        if not segments:
            raise RuntimeError(
                "Whisper returned no transcription segments. "
                "Verify src_lang matches the video's spoken language "
                f"(current value: '{src_lang}')."
            )
        total_dur = segments[-1]["end"] + 1.0

        # 3. Translate all segments
        segments = _translate_segments(segments, src_lang, tgt_lang)

        # 4a. SRT-only (no re-encode — fastest)
        if mode == "srt":
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(_segments_to_srt(segments))
            return {
                "ok": True, "action": "local_video_translate", "mode": "srt",
                "output": os.path.basename(out_path),
                "segments": len(segments),
                "src_lang": src_lang, "tgt_lang": tgt_lang,
                "bytes_out": os.path.getsize(out_path),
                "note": (f"Translated SRT exported ({src_lang}→{tgt_lang}). "
                         "Open with VLC (Subtitle menu) or burn with HandBrake."),
            }

        # 4b. Burn subtitles into video
        if mode == "subtitle":
            srt_path = os.path.join(work_dir, "subs.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(_segments_to_srt(segments))
            esc = srt_path.replace("\\", "/").replace(":", "\\:")
            _ffmpeg([
                "-i", video_path,
                "-vf", (f"subtitles={esc}:"
                        "force_style='FontSize=22,PrimaryColour=&H00FFFFFF,"
                        "OutlineColour=&H00000000,Outline=1,Bold=1'"),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                out_path,
            ], timeout=7200)
            return {
                "ok": True, "action": "local_video_translate", "mode": "subtitle",
                "output": os.path.basename(out_path),
                "segments": len(segments),
                "src_lang": src_lang, "tgt_lang": tgt_lang,
                "bytes_out": os.path.getsize(out_path),
                "note": (f"Subtitles burned in ({src_lang}→{tgt_lang}). "
                         "Whisper transcription + Google Translate."),
            }

        # 4c. Dub — replace audio with TTS
        if mode == "dub":
            tts_files  = _tts_generate_all(segments, work_dir, tgt_lang)
            dubbed_audio = _build_dubbed_audio(segments, work_dir, tts_files, total_dur)
            _ffmpeg([
                "-i", video_path, "-i", dubbed_audio,
                "-c:v", "copy",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest", out_path,
            ], timeout=7200)
            return {
                "ok": True, "action": "local_video_translate", "mode": "dub",
                "output": os.path.basename(out_path),
                "segments": len(segments),
                "src_lang": src_lang, "tgt_lang": tgt_lang,
                "bytes_out": os.path.getsize(out_path),
                "note": (f"Audio dubbed into {tgt_lang} ({src_lang}→{tgt_lang}). "
                         "TTS timing auto-adjusted per segment. "
                         "Whisper + Google Translate + edge-tts Neural voice."),
            }

        # 4d. Both — TTS voice dub + subtitles burned on screen
        #     Pass 1: swap original audio with TTS → temp_dubbed.mp4
        #     Pass 2: burn translated subtitles onto dubbed video → out_path
        if mode == "both":
            # Generate TTS audio track
            tts_files    = _tts_generate_all(segments, work_dir, tgt_lang)
            dubbed_audio = _build_dubbed_audio(segments, work_dir, tts_files, total_dur)

            # Pass 1 — swap audio (fast, copy video stream)
            temp_dubbed = os.path.join(work_dir, "temp_dubbed.mp4")
            _ffmpeg([
                "-i", video_path, "-i", dubbed_audio,
                "-c:v", "copy",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest", temp_dubbed,
            ], timeout=7200)

            # Pass 2 — burn subtitles onto the dubbed video
            srt_path = os.path.join(work_dir, "subs.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(_segments_to_srt(segments))
            esc = srt_path.replace("\\", "/").replace(":", "\\:")
            _ffmpeg([
                "-i", temp_dubbed,
                "-vf", (f"subtitles={esc}:"
                        "force_style='FontSize=22,PrimaryColour=&H00FFFFFF,"
                        "OutlineColour=&H00000000,Outline=1,Bold=1'"),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                out_path,
            ], timeout=7200)
            return {
                "ok": True, "action": "local_video_translate", "mode": "both",
                "output": os.path.basename(out_path),
                "segments": len(segments),
                "src_lang": src_lang, "tgt_lang": tgt_lang,
                "bytes_out": os.path.getsize(out_path),
                "note": (f"Full translation ({src_lang}→{tgt_lang}): "
                         f"{tgt_lang} TTS voice + {tgt_lang} subtitles burned on screen. "
                         "Whisper + Google Translate + edge-tts Neural voice."),
            }

        raise ValueError(
            f"unknown mode {mode!r}. Valid values: 'subtitle', 'srt', 'dub', 'both'."
        )

    finally:
        import shutil as _sh
        _sh.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def main():
    try:
        cmd = sys.argv[1]
        if cmd == "pdf2docx":
            res = pdf_to_docx(sys.argv[2], sys.argv[3])
        elif cmd == "pdf2docx_ocr":
            res = pdf_to_docx_ocr(sys.argv[2], sys.argv[3])
        elif cmd == "pdf2xlsx":
            res = pdf_to_xlsx(sys.argv[2], sys.argv[3])
        elif cmd == "pdf2img":
            fmt = sys.argv[4] if len(sys.argv) > 4 else "png"
            dpi = sys.argv[5] if len(sys.argv) > 5 else 150
            res = pdf_to_img(sys.argv[2], sys.argv[3], fmt, dpi)
        elif cmd == "img2relief":
            fmt = sys.argv[4] if len(sys.argv) > 4 else "obj"
            depth = sys.argv[5] if len(sys.argv) > 5 else 0.15
            res = img_to_relief(sys.argv[2], sys.argv[3], fmt, depth)
        elif cmd == "previewobj":
            depth = sys.argv[4] if len(sys.argv) > 4 else 0.15
            res = img_to_relief_raw(sys.argv[2], sys.argv[3], depth)
        elif cmd == "office2pdf":
            res = office_to_pdf(sys.argv[2], sys.argv[3])
        elif cmd == "img2pdf":
            res = image_to_pdf(sys.argv[2], sys.argv[3])
        elif cmd == "img_thermal":
            res = image_thermal(sys.argv[2], sys.argv[3])
        elif cmd == "img_xray":
            res = image_xray(sys.argv[2], sys.argv[3])
        elif cmd == "vid_thermal":
            res = video_thermal(sys.argv[2], sys.argv[3])
        elif cmd == "vid_anaglyph":
            res = video_anaglyph(sys.argv[2], sys.argv[3])
        elif cmd == "video2mp3":
            quality = sys.argv[4] if len(sys.argv) > 4 else "enhanced"
            res = video_to_mp3(sys.argv[2], sys.argv[3], quality)
        elif cmd == "video2ytmp4":
            res = video_to_youtube_mp4(sys.argv[2], sys.argv[3])
        elif cmd == "video2webm":
            res = video_to_webm(sys.argv[2], sys.argv[3])
        elif cmd == "img2img":
            fmt = sys.argv[4] if len(sys.argv) > 4 else "png"
            res = image_to_image(sys.argv[2], sys.argv[3], fmt)
        elif cmd == "img2docx":
            res = image_to_docx(sys.argv[2], sys.argv[3])
        elif cmd == "geolocate":
            res = geolocate(sys.argv[2])
        elif cmd == "medconvert":
            target = sys.argv[4] if len(sys.argv) > 4 else "png"
            res = medconvert(sys.argv[2], sys.argv[3], target)
        elif cmd == "dcmanon":
            res = dicom_anonymize(sys.argv[2], sys.argv[3])
        elif cmd == "rawconvert":
            fmt = sys.argv[4] if len(sys.argv) > 4 else "jpg"
            res = raw_convert(sys.argv[2], sys.argv[3], fmt)
        elif cmd == "scantext":
            res = scan_burned_text(sys.argv[2])
        elif cmd == "img2anaglyph":
            res = img_to_anaglyph(sys.argv[2], sys.argv[3])
        # ── iLovePDF-style PDF toolbox ───────────────────────────
        elif cmd == "pdf_merge":
            inputs = sys.argv[2].split("|")
            res = pdf_merge(inputs, sys.argv[3])
        elif cmd == "pdf_split":
            ranges = sys.argv[4] if len(sys.argv) > 4 else ""
            res = pdf_split(sys.argv[2], sys.argv[3], ranges)
        elif cmd == "pdf_remove":
            res = pdf_remove_pages(sys.argv[2], sys.argv[3], sys.argv[4])
        elif cmd == "pdf_extract":
            res = pdf_extract_pages(sys.argv[2], sys.argv[3], sys.argv[4])
        elif cmd == "pdf_rotate":
            deg = sys.argv[4] if len(sys.argv) > 4 else 90
            spec = sys.argv[5] if len(sys.argv) > 5 else ""
            res = pdf_rotate(sys.argv[2], sys.argv[3], deg, spec)
        elif cmd == "pdf_compress":
            level = sys.argv[4] if len(sys.argv) > 4 else "ebook"
            res = pdf_compress(sys.argv[2], sys.argv[3], level)
        elif cmd == "pdf_repair":
            res = pdf_repair(sys.argv[2], sys.argv[3])
        elif cmd == "pdf_protect":
            res = pdf_protect(sys.argv[2], sys.argv[3], sys.argv[4])
        elif cmd == "pdf_unlock":
            pw = sys.argv[4] if len(sys.argv) > 4 else ""
            res = pdf_unlock(sys.argv[2], sys.argv[3], pw)
        elif cmd == "pdf_pagenum":
            pos = sys.argv[4] if len(sys.argv) > 4 else "bottom-center"
            start = sys.argv[5] if len(sys.argv) > 5 else 1
            res = pdf_page_numbers(sys.argv[2], sys.argv[3], pos, start)
        elif cmd == "pdf_watermark":
            text = sys.argv[4] if len(sys.argv) > 4 else "CONFIDENTIAL"
            op = sys.argv[5] if len(sys.argv) > 5 else 0.15
            res = pdf_watermark(sys.argv[2], sys.argv[3], text, op)
        elif cmd == "pdf2pdfa":
            res = pdf_to_pdfa(sys.argv[2], sys.argv[3])
        elif cmd == "pdf2pptx":
            res = pdf_to_pptx(sys.argv[2], sys.argv[3])
        elif cmd == "pptx2pdf":
            res = pptx_to_pdf(sys.argv[2], sys.argv[3])
        elif cmd == "pdf2html":
            res = pdf_to_html(sys.argv[2], sys.argv[3])
        elif cmd == "html2pdf":
            res = html_to_pdf(sys.argv[2], sys.argv[3])
        elif cmd == "fixdocx":
            src = sys.argv[2]
            dst = sys.argv[3] if len(sys.argv) > 3 else src
            if dst != src:
                import shutil as _sh
                _sh.copyfile(src, dst)
            ok = _normalize_docx(dst)
            res = {"ok": True, "action": "fixdocx", "output": os.path.basename(dst),
                   "normalized": ok, "bytes_out": os.path.getsize(dst),
                   "note": (
                       "Fully repaired: LibreOffice schema rebuild + XML character clean. "
                       "Opens in Word 2007/2010/2013 on Windows 8.1."
                       if ok else
                       "XML characters cleaned (Stage 1). "
                       "Install LibreOffice for full schema repair: "
                       "sudo apt install libreoffice --no-install-recommends"
                   )}
        # ── YouTube Translation ──────────────────────────────────
        elif cmd == "youtube_translate":
            src   = sys.argv[4] if len(sys.argv) > 4 else "pt"
            tgt   = sys.argv[5] if len(sys.argv) > 5 else "fr"
            mode  = sys.argv[6] if len(sys.argv) > 6 else "subtitle"
            model = sys.argv[7] if len(sys.argv) > 7 else "base"
            res   = youtube_translate(sys.argv[2], sys.argv[3], src, tgt, mode, model)
        # ── Local Video Translation (file already on disk) ───────
        elif cmd == "local_video_translate":
            src   = sys.argv[4] if len(sys.argv) > 4 else "en"
            tgt   = sys.argv[5] if len(sys.argv) > 5 else "fr"
            mode  = sys.argv[6] if len(sys.argv) > 6 else "subtitle"
            model = sys.argv[7] if len(sys.argv) > 7 else "base"
            res   = local_video_translate(sys.argv[2], sys.argv[3], src, tgt, mode, model)
        else:
            raise ValueError(f"unknown conversion: {cmd}")
        out(res)
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": f"{type(e).__name__}: {e}"})
        sys.exit(1)


if __name__ == "__main__":
    main() 


