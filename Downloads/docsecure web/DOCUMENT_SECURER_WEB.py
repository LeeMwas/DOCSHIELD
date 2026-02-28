"""
Document Security System — Web Edition
=======================================
Runs a local Flask web server alongside the tkinter GUI.
Open https://localhost:5443 in any browser to use the web UI.
QR codes embed this URL so phone scanners redirect to your local server.

** ANDROID CAMERA FIX **
Android Chrome blocks camera (getUserMedia) on plain HTTP for non-localhost origins.
This version runs Flask over HTTPS using a self-signed certificate so phones on
the same Wi-Fi network can access the camera scanner tab.

On first run a self-signed cert is generated (docshield.crt / docshield.key).
Android / Chrome will show a "connection not private" warning — tap Advanced →
Proceed to <IP> to bypass it (this is normal for self-signed certs on LAN).

INSTALL DEPS:
    pip install flask flask-cors qrcode[pil] opencv-python pyzbar pillow numpy pymupdf scipy cryptography psycopg2-binary
"""

# ── Try to import tkinter (not available in cloud environments) ──
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    TKINTER_AVAILABLE = True
except (ImportError, RuntimeError):
    TKINTER_AVAILABLE = False
    # Mock tkinter objects for cloud-only environments
    class tk:
        Frame = object
        BooleanVar = lambda value=False: None
        Label = lambda **kwargs: None
        Button = lambda **kwargs: None
        StringVar = lambda value="": None
        Canvas = lambda **kwargs: None
        Entry = lambda **kwargs: None
        Text = lambda **kwargs: None
    ttk = None
    messagebox = None
    filedialog = None

import hashlib
import json
import os
import threading
import time
import tempfile
import webbrowser
import socket
from datetime import datetime
from difflib import SequenceMatcher
import re
import base64
import io

import qrcode
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
# ImageTk is only imported when tkinter is available
if TKINTER_AVAILABLE:
    from PIL import ImageTk
else:
    ImageTk = None
from pyzbar.pyzbar import decode, ZBarSymbol

# Flask
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS

# Optional imports
try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from scipy.fftpack import dct
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    def dct(x, norm=None):
        return x

import ctypes
try:
    ctypes.cdll.LoadLibrary("libzbar-0.dll")
except Exception:
    pass

# =============================================================================
# CONFIGURATION
# =============================================================================

DB_FILE = "issued_documents.json"   # kept as local fallback only
QR_FRACTION = 0.18
VISUAL_SIZE = 256
PERCEPTUAL_SIZE = 32
QUALITY_BLUR_THRESHOLD = 80
QUALITY_BRIGHTNESS_MIN = 30
QUALITY_BRIGHTNESS_MAX = 225
WEB_PORT = 5443          # HTTPS port — Android requires HTTPS for camera access
ADMIN_PORT = 5444        # Admin interface on separate port
CERT_FILE = "docshield.crt"
KEY_FILE  = "docshield.key"

# ── PostgreSQL connection (Render.com) ──────────────────────────────────────
DATABASE_URL = (
    "postgresql://doc_shield_user:KudGYk0cMyczIMSDgpUTkApibFbIxvX9"
    "@dpg-d6hbii7gi27c73fnjb20-a.oregon-postgres.render.com/doc_shield"
    "?sslmode=require"
)

# =============================================================================
# DATABASE LAYER  (PostgreSQL via psycopg2)
# =============================================================================

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    print("  [DB] psycopg2 not installed — run: pip install psycopg2-binary")

_db_conn = None                    # module-level persistent connection
_db_lock = threading.Lock()        # thread-safe access
_db_failed_time = 0                # timestamp of last failed connection attempt
_db_failure_cooldown = 30          # seconds between retry attempts after failure
_db_failure_logged = False         # flag to prevent repeated error messages


def _get_conn():
    """Return a live psycopg2 connection, reconnecting if needed.
    Uses exponential backoff to prevent spam on network failures."""
    global _db_conn, _db_failed_time, _db_failure_logged
    
    if not PSYCOPG2_AVAILABLE:
        return None
    
    # Check if we're in cooldown period after a failure
    if _db_failed_time > 0:
        elapsed = time.time() - _db_failed_time
        if elapsed < _db_failure_cooldown:
            return None
        # Reset after cooldown expires
        _db_failed_time = 0
        _db_failure_logged = False
    
    with _db_lock:
        try:
            if _db_conn is None or _db_conn.closed:
                _db_conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
                _db_conn.autocommit = False
            else:
                # ping
                with _db_conn.cursor() as cur:
                    cur.execute("SELECT 1")
                _db_conn.commit()
            return _db_conn
        except Exception as e:
            # Log error only once per failure cycle
            if not _db_failure_logged:
                error_msg = str(e)
                if "Name or service not known" in error_msg or "could not translate" in error_msg:
                    print(f"  [DB] Connection failed: Cannot reach Supabase (network/DNS issue)")
                    print(f"  [DB] Details: {error_msg}")
                else:
                    print(f"  [DB] Reconnect failed: {e}")
                _db_failure_logged = True
            
            _db_failed_time = time.time()  # Start cooldown
            _db_conn = None
            return None


def init_db():
    """Create the documents table if it does not exist."""
    conn = _get_conn()
    if not conn:
        print("  [DB] 📄 Using local JSON file for document storage.")
        return False
    try:
        with _db_lock:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id               SERIAL PRIMARY KEY,
                        doc_id           TEXT UNIQUE NOT NULL,
                        holder_name      TEXT NOT NULL,
                        doc_type         TEXT NOT NULL,
                        issue_date       TEXT NOT NULL,
                        expiry_date      TEXT,
                        additional       TEXT,
                        file_hash        TEXT,
                        visual_hash      TEXT,
                        perceptual_hash  TEXT,
                        text_features    JSONB,
                        bound_hash       TEXT,
                        verify_url       TEXT,
                        issued_at        TIMESTAMPTZ DEFAULT NOW()
                    );
                """)
            conn.commit()
        print("  [DB] ✅ Connected to Supabase PostgreSQL — table ready.")
        return True
    except Exception as e:
        print(f"  [DB] Table creation error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def save_to_registry(doc: dict):
    """Save document record to PostgreSQL (falls back to local JSON)."""
    conn = _get_conn()
    if conn:
        try:
            with _db_lock:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO documents
                            (doc_id, holder_name, doc_type, issue_date, expiry_date,
                             additional, file_hash, visual_hash, perceptual_hash,
                             text_features, bound_hash, verify_url)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (doc_id) DO UPDATE SET
                            holder_name     = EXCLUDED.holder_name,
                            doc_type        = EXCLUDED.doc_type,
                            issue_date      = EXCLUDED.issue_date,
                            expiry_date     = EXCLUDED.expiry_date,
                            additional      = EXCLUDED.additional,
                            file_hash       = EXCLUDED.file_hash,
                            visual_hash     = EXCLUDED.visual_hash,
                            perceptual_hash = EXCLUDED.perceptual_hash,
                            text_features   = EXCLUDED.text_features,
                            bound_hash      = EXCLUDED.bound_hash,
                            verify_url      = EXCLUDED.verify_url;
                    """, (
                        doc["doc_id"], doc["holder_name"], doc["doc_type"],
                        doc["issue_date"], doc.get("expiry_date", ""),
                        doc.get("additional", ""), doc.get("file_hash", ""),
                        doc.get("visual_hash", ""), doc.get("perceptual_hash", ""),
                        json.dumps(doc.get("text_features")) if doc.get("text_features") else None,
                        doc.get("hash", ""), doc.get("verify_url", ""),
                    ))
                conn.commit()
            print(f"  [DB] ✅ Saved doc_id={doc['doc_id']} to PostgreSQL.")
            return
        except Exception as e:
            # Silent fallback - DB connection issues already logged in _get_conn()
            try:
                conn.rollback()
            except Exception:
                pass

    # ── JSON fallback ──
    records = []
    if os.path.exists(DB_FILE):
        with open(DB_FILE) as f:
            records = json.load(f)
    records.append({"document": doc, "issued_date": datetime.now().isoformat()})
    with open(DB_FILE, "w") as f:
        json.dump(records, f, indent=2)


def load_registry() -> list:
    """Load all document records from PostgreSQL (falls back to local JSON)."""
    conn = _get_conn()
    if conn:
        try:
            with _db_lock:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT doc_id, holder_name, doc_type, issue_date, expiry_date,
                               additional, file_hash, visual_hash, perceptual_hash,
                               text_features, bound_hash AS hash, verify_url,
                               issued_at::text AS timestamp
                        FROM documents ORDER BY issued_at DESC;
                    """)
                    rows = cur.fetchall()
            records = []
            for row in rows:
                d = dict(row)
                # Normalise text_features (stored as JSONB → dict)
                if d.get("text_features") and isinstance(d["text_features"], str):
                    try:
                        d["text_features"] = json.loads(d["text_features"])
                    except Exception:
                        d["text_features"] = None
                records.append({
                    "document": d,
                    "issued_date": d.get("timestamp", ""),
                })
            return records
        except Exception as e:
            # Silent fallback - DB connection issues already logged in _get_conn()
            pass

    # ── JSON fallback ──
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE) as f:
        return json.load(f)


def generate_self_signed_cert(cert_path: str, key_path: str, ip: str) -> bool:
    """Generate a self-signed TLS certificate so Flask can serve HTTPS.
    Android Chrome blocks getUserMedia() on plain HTTP for non-localhost origins.
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        import ipaddress, datetime as _dt

        key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend())
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "DocShield Local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DocShield"),
        ])
        san = x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.IPv4Address(ip)),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.DNSName("localhost"),
        ])
        cert = (x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(_dt.datetime.utcnow())
                .not_valid_after(_dt.datetime.utcnow() + _dt.timedelta(days=3650))
                .add_extension(san, critical=False)
                .sign(key, hashes.SHA256(), default_backend()))

        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()))
        print(f"  [SSL] Self-signed cert generated: {cert_path}")
        return True
    except Exception as e:
        print(f"  [SSL] Could not generate cert ({e}).")
        print(f"  [SSL] Install cryptography: pip install cryptography")
        print(f"  [SSL] Falling back to HTTP (camera WILL NOT work on Android).")
        return False

def get_local_ip():
    """Get the machine's LAN IP so phones on same WiFi can connect."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()

# =============================================================================
# CORE HASHING FUNCTIONS  (unchanged from original)
# =============================================================================

def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def visual_fingerprint(img: Image.Image) -> str:
    gray = img.convert("L").copy()
    w, h = gray.size
    draw = ImageDraw.Draw(gray)
    stamp = int(min(w, h) * 0.30)
    draw.rectangle([w - stamp, h - stamp, w, h], fill=128)
    thumb = gray.resize((VISUAL_SIZE, VISUAL_SIZE), Image.LANCZOS)
    return hashlib.sha256(thumb.tobytes()).hexdigest()

def perceptual_hash(img: Image.Image) -> str:
    try:
        gray = img.convert("L")
        w, h = gray.size
        draw = ImageDraw.Draw(gray)
        stamp = int(min(w, h) * 0.30)
        draw.rectangle([w - stamp, h - stamp, w, h], fill=128)
        small = gray.resize((8, 8), Image.LANCZOS)
        pixels = np.array(small).astype(float)
        avg = np.mean(pixels)
        hash_bits = (pixels > avg).flatten()
        hash_int = 0
        for i, bit in enumerate(hash_bits[:64]):
            if bit:
                hash_int |= (1 << i)
        return format(hash_int, '016x')
    except Exception:
        return visual_fingerprint(img)[:16]

def compare_perceptual_hash(hash1: str, hash2: str) -> float:
    if not hash1 or not hash2 or len(hash1) < 16 or len(hash2) < 16:
        return 0.0
    try:
        h1_bin = bin(int(hash1[:16], 16))[2:].zfill(64)
        h2_bin = bin(int(hash2[:16], 16))[2:].zfill(64)
        matches = sum(c1 == c2 for c1, c2 in zip(h1_bin, h2_bin))
        return matches / 64.0
    except Exception:
        return 0.0

def extract_text_features(img: Image.Image):
    try:
        gray = img.convert("L")
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(2.0)
        w, h = enhanced.size
        draw = ImageDraw.Draw(enhanced)
        stamp = int(min(w, h) * 0.30)
        draw.rectangle([w - stamp, h - stamp, w, h], fill=255)
        pixels = np.array(enhanced)
        return {
            'mean_intensity': float(np.mean(pixels)),
            'std_intensity': float(np.std(pixels)),
            'size_ratio': w / h,
            'pixel_count': w * h
        }
    except Exception:
        return None

def compare_text_features(stored, uploaded) -> float:
    if not stored or not uploaded:
        return 0.5
    try:
        scores = []
        if stored.get('mean_intensity') and uploaded.get('mean_intensity'):
            ratio = min(stored['mean_intensity'], uploaded['mean_intensity']) / \
                   max(stored['mean_intensity'], uploaded['mean_intensity'])
            scores.append(min(1.0, ratio * 1.2))
        if stored.get('std_intensity') and uploaded.get('std_intensity'):
            ratio = min(stored['std_intensity'], uploaded['std_intensity']) / \
                   max(stored['std_intensity'], uploaded['std_intensity'])
            scores.append(min(1.0, ratio * 1.3))
        if stored.get('size_ratio') and uploaded.get('size_ratio'):
            ratio = min(stored['size_ratio'], uploaded['size_ratio']) / \
                   max(stored['size_ratio'], uploaded['size_ratio'])
            scores.append(ratio)
        if scores:
            return sum(scores) / len(scores)
    except Exception:
        pass
    return 0.5

def check_photo_quality(img: Image.Image):
    try:
        img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        blur_variance = cv2.Laplacian(gray, cv2.CV_64F).var()
        mean_brightness = np.mean(gray)
        quality_score = 100
        if blur_variance < QUALITY_BLUR_THRESHOLD:
            quality_score -= 30
        if mean_brightness < QUALITY_BRIGHTNESS_MIN:
            quality_score -= 20
        elif mean_brightness > QUALITY_BRIGHTNESS_MAX:
            quality_score -= 20
        quality_ok = quality_score >= 50
        quality_msg = f"Quality: {quality_score:.0f}% (blur: {blur_variance:.1f}, brightness: {mean_brightness:.1f})"
        return quality_ok, quality_msg
    except Exception as e:
        return True, f"Quality check skipped: {e}"

def preprocess_photo(img: Image.Image) -> Image.Image:
    try:
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.3)
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(1.2)
        return img
    except Exception:
        return img

# =============================================================================
# DOCUMENT PROCESSING
# =============================================================================

def file_to_pil(path: str, dpi: int = 150) -> Image.Image:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        if not PYMUPDF_AVAILABLE:
            raise RuntimeError("Please install PyMuPDF: pip install pymupdf")
        doc = fitz.open(path)
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = doc[0].get_pixmap(matrix=mat, alpha=False)
        doc.close()
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    else:
        return Image.open(path).convert("RGB")

def pil_from_bytes(data: bytes, filename: str = "") -> Image.Image:
    """Load PIL image from raw bytes (for web uploads)."""
    ext = os.path.splitext(filename)[1].lower() if filename else ""
    if ext == ".pdf":
        if not PYMUPDF_AVAILABLE:
            raise RuntimeError("Install PyMuPDF: pip install pymupdf")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(data)
        tmp.close()
        try:
            return file_to_pil(tmp.name)
        finally:
            os.unlink(tmp.name)
    else:
        return Image.open(io.BytesIO(data)).convert("RGB")

def make_bound_hash(doc_id, holder, doc_type, issue_date, file_hash):
    raw = f"{doc_id}|{holder}|{doc_type}|{issue_date}|{file_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()

def make_qr_payload(doc_id, bound_hash, verify_url=None):
    """QR payload now includes the web verify URL so phone scanners redirect there."""
    data = {"doc_id": doc_id, "hash": bound_hash}
    if verify_url:
        data["url"] = verify_url
    # The QR code TEXT itself is the verify URL — phones will open it as a link
    # but we also embed doc_id and hash in the URL params for verification
    if verify_url:
        return verify_url  # URL format: http://IP:5000/?verify=DOCID&hash=HASH
    return json.dumps(data, separators=(",", ":"))

def build_verify_url(doc_id: str, bound_hash: str) -> str:
    """Build the full verify URL that will be embedded in QR codes."""
    base = f"https://{LOCAL_IP}:{WEB_PORT}"
    return f"{base}/?verify={doc_id}&hash={bound_hash}"

def generate_qr_pil(payload: str, size_px: int = 300) -> Image.Image:
    qr = qrcode.QRCode(version=None, box_size=10, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size_px, size_px), Image.NEAREST)

def embed_qr_into_pdf(src_path: str, qr_pil: Image.Image, out_path: str):
    if not PYMUPDF_AVAILABLE:
        raise RuntimeError("Please install PyMuPDF: pip install pymupdf")
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    qr_pil.save(tmp.name)
    tmp.close()
    try:
        doc = fitz.open(src_path)
        page = doc[0]
        pw, ph = page.rect.width, page.rect.height
        qr_size = min(pw, ph) * QR_FRACTION
        margin = 14
        label_h = 14
        x1 = pw - qr_size - margin
        y1 = ph - qr_size - margin - label_h
        x2 = pw - margin
        y2 = ph - margin - label_h
        page.draw_rect(
            fitz.Rect(x1 - 5, y1 - 5, x2 + 5, ph - margin + 3),
            color=(0.85, 0.85, 0.85), fill=(1, 1, 1))
        page.insert_image(fitz.Rect(x1, y1, x2, y2), filename=tmp.name)
        page.insert_text(
            fitz.Point(x1, ph - margin - 1),
            "SECURITY QR — Scan to verify",
            fontsize=6.5, color=(0.3, 0.3, 0.3))
        doc.save(out_path, garbage=4, deflate=True)
        doc.close()
    finally:
        os.unlink(tmp.name)

def embed_qr_into_image(src_path: str, qr_pil: Image.Image, out_path: str):
    base = Image.open(src_path).convert("RGB")
    bw, bh = base.size
    qr_size = int(min(bw, bh) * QR_FRACTION)
    qr_img = qr_pil.resize((qr_size, qr_size), Image.NEAREST)
    margin = 10
    label_h = 16
    box_w = qr_size + margin * 2
    box_h = qr_size + margin * 2 + label_h
    backing = Image.new("RGB", (box_w, box_h), (255, 255, 255))
    draw = ImageDraw.Draw(backing)
    try:
        font = ImageFont.truetype("arial.ttf", 10)
    except:
        font = ImageFont.load_default()
    draw.text((4, qr_size + margin + 2), "SECURITY QR — Scan to verify",
              fill=(80, 80, 80), font=font)
    draw.rectangle([0, 0, box_w - 1, box_h - 1], outline=(160, 160, 160), width=1)
    backing.paste(qr_img, (margin, margin))
    bx = bw - box_w - margin
    by = bh - box_h - margin
    base.paste(backing, (bx, by))
    ext = os.path.splitext(out_path)[1].lower().lstrip(".")
    base.save(out_path, {"jpg": "JPEG", "jpeg": "JPEG"}.get(ext, "PNG"))

def extract_qr_from_array(frame: np.ndarray):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    codes = decode(gray, symbols=[ZBarSymbol.QRCODE])
    if codes:
        return codes[0].data.decode()
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    codes = decode(enhanced, symbols=[ZBarSymbol.QRCODE])
    if codes:
        return codes[0].data.decode()
    return None

def extract_qr_from_pil(pil_img: Image.Image):
    """Extract QR code string from a PIL image."""
    frame = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return extract_qr_from_array(frame)

def hash_image_array_camera(arr: np.ndarray) -> str:
    small = cv2.resize(arr, (128, 128), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return hashlib.sha256(gray.tobytes()).hexdigest()

# =============================================================================
# UNIFIED VERIFY FUNCTION  (used by both GUI and web API)
# =============================================================================

def verify_document_full(pil_img: Image.Image, is_physical: bool = False,
                          doc_id_hint: str = None, hash_hint: str = None):
    """
    Full document verification.
    Tries:
      1. QR code embedded in the document image
      2. URL params (doc_id_hint + hash_hint) passed from web UI
    Returns dict: {valid, verdict, message, document, confidence}
    """
    doc_id = None
    qr_hash = None

    # ── Step 1: Try to read QR from image ──
    qr_str = extract_qr_from_pil(pil_img)

    if qr_str:
        # Could be a URL or JSON
        try:
            # Try URL first
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(qr_str)
            params = parse_qs(parsed.query)
            if "verify" in params:
                doc_id = params["verify"][0]
            if "hash" in params:
                qr_hash = params["hash"][0]
        except:
            pass

        if not doc_id:
            try:
                payload = json.loads(qr_str)
                doc_id = payload.get("doc_id", "").strip()
                qr_hash = payload.get("hash", "").strip()
            except:
                pass

    # ── Step 2: Fallback to URL hints passed from web ──
    if not doc_id and doc_id_hint:
        doc_id = doc_id_hint.strip()
    if not qr_hash and hash_hint:
        qr_hash = hash_hint.strip()

    if not doc_id:
        return {
            "valid": False,
            "verdict": "NO QR FOUND",
            "message": "Could not detect a QR code in this document. Make sure the document has a DocShield QR stamp.",
            "document": None,
            "confidence": 0.0
        }

    # ── Step 3: Registry lookup ──
    records = load_registry()
    match = None
    for rec in records:
        if rec.get("document", {}).get("doc_id") == doc_id:
            match = rec
            break

    if not match:
        return {
            "valid": False,
            "verdict": "NOT IN REGISTRY",
            "message": f"Document ID '{doc_id}' was not found in the registry. This document may be fraudulent or was issued elsewhere.",
            "document": None,
            "confidence": 0.0
        }

    doc = match["document"]

    # ── Step 4: Hash verification ──
    if qr_hash:
        expected_hash = make_bound_hash(
            doc["doc_id"], doc["holder_name"],
            doc["doc_type"], doc["issue_date"],
            doc.get("file_hash", "")
        )
        if qr_hash != expected_hash:
            return {
                "valid": False,
                "verdict": "TAMPERED",
                "message": "QR code hash does not match registry. This document has been tampered with or forged.",
                "document": doc,
                "confidence": 0.0
            }

    # ── Step 5: Visual/perceptual content verification ──
    if is_physical:
        quality_ok, quality_msg = check_photo_quality(pil_img)
        if not quality_ok:
            return {
                "valid": False,
                "verdict": "POOR PHOTO QUALITY",
                "message": f"Photo quality too low for reliable verification. {quality_msg}. Please retake with better lighting.",
                "document": doc,
                "confidence": 0.0
            }
        pil_img = preprocess_photo(pil_img)

        if doc.get("perceptual_hash"):
            phash = perceptual_hash(pil_img)
            phash_score = compare_perceptual_hash(doc["perceptual_hash"], phash)
            text_score = 0.5
            if doc.get("text_features"):
                text_feat = extract_text_features(pil_img)
                text_score = compare_text_features(doc["text_features"], text_feat)
            confidence = phash_score * 0.7 + text_score * 0.3
            if confidence >= 0.65:
                return {"valid": True, "verdict": "AUTHENTIC",
                        "message": "Document is genuine. Content matches original on file.",
                        "document": doc, "confidence": confidence}
            else:
                return {"valid": False, "verdict": "CONTENT MISMATCH",
                        "message": f"Document content does not match original ({confidence:.0%} similarity). Possible substitution or forgery.",
                        "document": doc, "confidence": confidence}
        else:
            vhash = visual_fingerprint(pil_img)
            if vhash == doc.get("visual_hash", ""):
                return {"valid": True, "verdict": "AUTHENTIC",
                        "message": "Document is genuine.", "document": doc, "confidence": 1.0}
            else:
                return {"valid": False, "verdict": "CONTENT MISMATCH",
                        "message": "Document content does not match original.",
                        "document": doc, "confidence": 0.0}
    else:
        # Digital mode — exact visual hash
        vhash = visual_fingerprint(pil_img)
        if vhash == doc.get("visual_hash", ""):
            return {"valid": True, "verdict": "AUTHENTIC",
                    "message": "Digital document is genuine. Pixel-perfect match confirmed.",
                    "document": doc, "confidence": 1.0}
        else:
            return {"valid": False, "verdict": "MODIFIED",
                    "message": "Document has been digitally altered since issuance.",
                    "document": doc, "confidence": 0.0}


def lookup_doc_by_id(doc_id: str) -> dict | None:
    """Fetch a single document record from Supabase by doc_id (fast path)."""
    conn = _get_conn()
    if conn:
        try:
            with _db_lock:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT doc_id, holder_name, doc_type, issue_date, expiry_date,
                               additional, file_hash, visual_hash, perceptual_hash,
                               text_features, bound_hash AS hash, verify_url,
                               issued_at::text AS timestamp
                        FROM documents WHERE doc_id = %s LIMIT 1;
                    """, (doc_id,))
                    row = cur.fetchone()
            if row:
                d = dict(row)
                if d.get("text_features") and isinstance(d["text_features"], str):
                    try:
                        d["text_features"] = json.loads(d["text_features"])
                    except Exception:
                        d["text_features"] = None
                return d
        except Exception as e:
            print(f"  [DB] lookup_doc_by_id failed ({e}), falling back to full load.")

    # Fallback: scan full registry
    for rec in load_registry():
        if rec.get("document", {}).get("doc_id") == doc_id:
            return rec["document"]
    return None


def verify_by_id_only(doc_id: str, qr_hash: str = None):
    """Verify just by document ID (for manual/camera QR scan). Uses fast DB lookup."""
    doc = lookup_doc_by_id(doc_id)

    if not doc:
        return {
            "valid": False,
            "verdict": "NOT IN REGISTRY",
            "message": f"Document ID '{doc_id}' not found in registry.",
            "document": None,
            "confidence": 0.0
        }

    if qr_hash:
        expected = make_bound_hash(
            doc["doc_id"], doc["holder_name"],
            doc["doc_type"], doc["issue_date"],
            doc.get("file_hash", "")
        )
        if qr_hash != expected:
            return {
                "valid": False,
                "verdict": "TAMPERED",
                "message": "Hash mismatch — document may have been forged.",
                "document": doc,
                "confidence": 0.0
            }

    return {
        "valid": True,
        "verdict": "AUTHENTIC",
        "message": "Document ID and hash verified against registry.",
        "document": doc,
        "confidence": 1.0
    }

# =============================================================================
# FLASK WEB SERVER
# =============================================================================

flask_app = Flask(__name__)
CORS(flask_app)

# ── Inline HTML (the full frontend) ──
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DocShield — Document Verification</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap" rel="stylesheet">
<style>
  :root {
    --ink: #0a0a0f;
    --paper: #f5f0e8;
    --shield: #00e5a0;
    --alert: #ff3c5a;
    --dim: #1e1e2e;
    --border: rgba(245,240,232,0.12);
    --glass: rgba(245,240,232,0.06);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--ink); color: var(--paper);
    font-family: 'Syne', sans-serif; min-height: 100vh; overflow-x: hidden;
  }
  body::before {
    content: ''; position: fixed; inset: 0;
    background-image: linear-gradient(rgba(0,229,160,0.04) 1px,transparent 1px),linear-gradient(90deg,rgba(0,229,160,0.04) 1px,transparent 1px);
    background-size: 40px 40px; pointer-events: none; z-index: 0;
  }
  body::after {
    content: ''; position: fixed; top: -20%; left: 50%; transform: translateX(-50%);
    width: 80vw; height: 60vh;
    background: radial-gradient(ellipse at center,rgba(0,229,160,0.08) 0%,transparent 70%);
    pointer-events: none; z-index: 0;
  }
  .container { position: relative; z-index: 1; max-width: 920px; margin: 0 auto; padding: 0 20px; }
  header { padding: 36px 0 16px; text-align: center; }
  .logo-row { display: inline-flex; align-items: center; gap: 14px; margin-bottom: 14px; }
  .shield-svg { width: 52px; height: 52px; filter: drop-shadow(0 0 14px var(--shield)); animation: pulse 3s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{filter:drop-shadow(0 0 8px var(--shield))}50%{filter:drop-shadow(0 0 22px var(--shield)) drop-shadow(0 0 44px rgba(0,229,160,.3))} }
  .brand { font-size: 2.3rem; font-weight: 800; letter-spacing: -.02em; background: linear-gradient(135deg,var(--paper) 40%,var(--shield)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
  .tagline { font-family: 'Space Mono',monospace; font-size: .68rem; color: rgba(245,240,232,.38); letter-spacing: .22em; text-transform: uppercase; margin-top: 4px; }
  .url-banner { background: var(--glass); border: 1px solid var(--border); border-radius: 12px; padding: 11px 18px; margin: 14px 0 28px; display: flex; align-items: center; gap: 12px; backdrop-filter: blur(10px); }
  .url-label { font-family: 'Space Mono',monospace; font-size: .62rem; color: var(--shield); letter-spacing: .16em; text-transform: uppercase; white-space: nowrap; }
  .url-val { font-family: 'Space Mono',monospace; font-size: .78rem; color: var(--paper); flex: 1; word-break: break-all; }
  .copy-btn { background: none; border: 1px solid var(--border); color: var(--paper); padding: 5px 12px; border-radius: 6px; cursor: pointer; font-family: 'Space Mono',monospace; font-size: .62rem; letter-spacing: .1em; text-transform: uppercase; transition: all .2s; white-space: nowrap; }
  .copy-btn:hover { background: var(--shield); color: var(--ink); border-color: var(--shield); }
  .tabs { display: flex; gap: 4px; margin-bottom: 26px; background: var(--glass); border: 1px solid var(--border); border-radius: 14px; padding: 5px; backdrop-filter: blur(10px); }
  .tab { flex: 1; padding: 12px 8px; border: none; background: transparent; color: rgba(245,240,232,.42); font-family: 'Syne',sans-serif; font-size: .82rem; font-weight: 600; cursor: pointer; border-radius: 10px; transition: all .25s; display: flex; align-items: center; justify-content: center; gap: 7px; }
  .tab.active { background: var(--shield); color: var(--ink); }
  .tab:hover:not(.active) { color: var(--paper); background: rgba(245,240,232,.07); }
  .panel { display: none; animation: fi .3s ease; }
  .panel.active { display: block; }
  @keyframes fi { from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)} }
  .card { background: var(--glass); border: 1px solid var(--border); border-radius: 20px; padding: 30px; backdrop-filter: blur(10px); margin-bottom: 20px; }
  .card-title { font-family: 'Space Mono',monospace; font-size: .72rem; color: var(--shield); letter-spacing: .1em; text-transform: uppercase; margin-bottom: 20px; display: flex; align-items: center; gap: 8px; }
  .scanner-wrap { position: relative; width: 100%; max-width: 460px; margin: 0 auto; aspect-ratio: 1; border-radius: 16px; overflow: hidden; background: #000; }
  #qr-video { width: 100%; height: 100%; object-fit: cover; display: block; }
  .scan-overlay { position: absolute; inset: 0; pointer-events: none; }
  .scan-frame { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 65%; height: 65%; }
  .scan-frame::before,.scan-frame::after,.scan-frame span::before,.scan-frame span::after { content:''; position:absolute; width:24px; height:24px; border-color:var(--shield); border-style:solid; }
  .scan-frame::before { top:0;left:0;border-width:3px 0 0 3px; }
  .scan-frame::after  { top:0;right:0;border-width:3px 3px 0 0; }
  .scan-frame span::before { bottom:0;left:0;border-width:0 0 3px 3px; }
  .scan-frame span::after  { bottom:0;right:0;border-width:0 3px 3px 0; }
  .scan-line { position:absolute;left:17.5%;width:65%;height:2px;background:linear-gradient(90deg,transparent,var(--shield),transparent);box-shadow:0 0 8px var(--shield);animation:scanl 2.5s ease-in-out infinite;top:17.5%; }
  @keyframes scanl { 0%{top:17.5%;opacity:1}50%{top:82.5%;opacity:1}51%{opacity:0}52%{top:17.5%}100%{top:17.5%;opacity:1} }
  .scan-status { position:absolute;bottom:0;left:0;right:0;padding:14px;background:linear-gradient(transparent,rgba(10,10,15,.9));font-family:'Space Mono',monospace;font-size:.7rem;text-align:center;color:rgba(245,240,232,.6);letter-spacing:.08em; }
  .btn { display:inline-flex;align-items:center;gap:8px;padding:13px 24px;border:none;border-radius:10px;font-family:'Syne',sans-serif;font-size:.9rem;font-weight:600;cursor:pointer;transition:all .2s;letter-spacing:.02em; }
  .btn-primary { background:var(--shield);color:var(--ink);width:100%;justify-content:center;margin-top:14px; }
  .btn-primary:hover { background:#00ffc0;transform:translateY(-1px);box-shadow:0 6px 24px rgba(0,229,160,.3); }
  .btn-primary:disabled { opacity:.4;cursor:not-allowed;transform:none; }
  .btn-secondary { background:var(--glass);color:var(--paper);border:1px solid var(--border);width:100%;justify-content:center;margin-top:10px; }
  .btn-secondary:hover { background:rgba(245,240,232,.1); }
  .drop-zone { border:2px dashed var(--border);border-radius:16px;padding:44px 24px;text-align:center;cursor:pointer;transition:all .3s;background:rgba(0,0,0,.2);position:relative; }
  .drop-zone:hover,.drop-zone.drag-over { border-color:var(--shield);background:rgba(0,229,160,.04); }
  .drop-zone input[type=file] { position:absolute;inset:0;opacity:0;cursor:pointer; }
  .drop-icon { font-size:2.4rem;margin-bottom:12px;display:block; }
  .drop-title { font-size:1rem;font-weight:600;margin-bottom:6px; }
  .drop-sub { font-family:'Space Mono',monospace;font-size:.66rem;color:rgba(245,240,232,.38);letter-spacing:.08em;text-transform:uppercase; }
  .field-group { margin-bottom:16px; }
  .field-label { font-family:'Space Mono',monospace;font-size:.63rem;color:var(--shield);letter-spacing:.15em;text-transform:uppercase;margin-bottom:8px;display:block; }
  .field-input { width:100%;background:rgba(0,0,0,.3);border:1px solid var(--border);border-radius:10px;padding:12px 16px;color:var(--paper);font-family:'Space Mono',monospace;font-size:.84rem;outline:none;transition:border-color .2s; }
  .field-input:focus { border-color:var(--shield); }
  .field-input::placeholder { color:rgba(245,240,232,.24); }
  .toggle-row { display:flex;align-items:center;gap:10px;margin-bottom:14px; }
  .toggle-label { font-family:'Space Mono',monospace;font-size:.68rem;color:rgba(245,240,232,.5);letter-spacing:.08em;text-transform:uppercase; }
  .toggle { width:44px;height:24px;background:var(--border);border-radius:99px;position:relative;cursor:pointer;transition:background .2s; }
  .toggle.on { background:var(--shield); }
  .toggle::after { content:'';position:absolute;width:18px;height:18px;background:white;border-radius:50%;top:3px;left:3px;transition:transform .2s; }
  .toggle.on::after { transform:translateX(20px); }
  .result-box { border-radius:16px;padding:24px;margin-top:22px;display:none;animation:ra .4s cubic-bezier(.34,1.56,.64,1); }
  @keyframes ra { from{opacity:0;transform:scale(.92)}to{opacity:1;transform:scale(1)} }
  .result-box.show { display:block; }
  .result-box.legit { background:rgba(0,229,160,.08);border:1.5px solid var(--shield); }
  .result-box.fake  { background:rgba(255,60,90,.08);border:1.5px solid var(--alert); }
  .result-box.loading { background:rgba(245,240,232,.04);border:1.5px solid var(--border); }
  .verdict { font-size:1.55rem;font-weight:800;margin-bottom:8px;display:flex;align-items:center;gap:12px; }
  .result-box.legit .verdict { color:var(--shield); }
  .result-box.fake  .verdict { color:var(--alert); }
  .result-box.loading .verdict { color:rgba(245,240,232,.6); }
  .detail { font-family:'Space Mono',monospace;font-size:.73rem;line-height:1.8;color:rgba(245,240,232,.65); }
  .detail strong { color:var(--paper); }
  .conf-row { margin-top:14px;display:flex;align-items:center;gap:12px; }
  .conf-lbl { font-family:'Space Mono',monospace;font-size:.63rem;color:rgba(245,240,232,.38);text-transform:uppercase;letter-spacing:.1em;white-space:nowrap; }
  .conf-bg { flex:1;height:5px;background:rgba(245,240,232,.1);border-radius:99px;overflow:hidden; }
  .conf-fill { height:100%;border-radius:99px;transition:width .8s cubic-bezier(.34,1.56,.64,1);background:var(--shield); }
  .conf-fill.low { background:var(--alert); }
  .conf-pct { font-family:'Space Mono',monospace;font-size:.68rem;color:var(--paper);min-width:36px;text-align:right; }
  .preview-wrap { margin-top:16px;display:none;border-radius:12px;overflow:hidden;border:1px solid var(--border);max-height:200px; }
  .preview-wrap.show { display:block; }
  .preview-wrap img { width:100%;height:200px;object-fit:contain;background:rgba(0,0,0,.3); }
  .qr-output-row { display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap;margin-top:8px; }
  .qr-canvas-box { background:white;border-radius:12px;padding:16px;display:flex;align-items:center;justify-content:center;min-width:180px;min-height:180px; }
  .qr-info { flex:1;min-width:200px; }
  .qr-info-row { display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid var(--border);font-family:'Space Mono',monospace;font-size:.7rem; }
  .qr-info-row:last-child { border-bottom:none; }
  .qi-key { color:rgba(245,240,232,.38);flex:0 0 72px; }
  .qi-val { color:var(--paper);word-break:break-all; }
  .steps { display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-top:8px; }
  .step { background:rgba(0,0,0,.2);border:1px solid var(--border);border-radius:14px;padding:18px; }
  .step-num { font-family:'Space Mono',monospace;font-size:.62rem;color:var(--shield);letter-spacing:.15em;margin-bottom:8px; }
  .step-title { font-weight:600;font-size:.88rem;margin-bottom:6px; }
  .step-desc { font-size:.76rem;color:rgba(245,240,232,.48);line-height:1.6; }
  .spinner { width:26px;height:26px;border:3px solid rgba(245,240,232,.1);border-top-color:var(--shield);border-radius:50%;animation:spin .8s linear infinite;display:inline-block; }
  @keyframes spin { to{transform:rotate(360deg)} }
  footer { text-align:center;padding:36px 0 28px;font-family:'Space Mono',monospace;font-size:.62rem;color:rgba(245,240,232,.18);letter-spacing:.12em;text-transform:uppercase; }
  .toast { position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(100px);background:var(--dim);border:1px solid var(--shield);color:var(--paper);padding:12px 24px;border-radius:999px;font-family:'Space Mono',monospace;font-size:.73rem;letter-spacing:.08em;z-index:9999;transition:transform .3s cubic-bezier(.34,1.56,.64,1); }
  .toast.show { transform:translateX(-50%) translateY(0); }
  .network-note { font-family:'Space Mono',monospace;font-size:.65rem;color:rgba(245,240,232,.35);margin-top:6px;letter-spacing:.06em; }
  #qr-placeholder { text-align:center;padding:32px;color:rgba(245,240,232,.22);font-family:'Space Mono',monospace;font-size:.72rem;letter-spacing:.1em;margin-top:8px; }
  @media(max-width:600px){.card{padding:18px}.brand{font-size:1.7rem}.qr-output-row{flex-direction:column}.qr-canvas-box{width:100%}}
</style>
</head>
<body>
<div class="container">

  <header>
    <div class="logo-row">
      <svg class="shield-svg" viewBox="0 0 48 48" fill="none">
        <path d="M24 4L6 12v12c0 10 8 19.2 18 22 10-2.8 18-12 18-22V12L24 4z" fill="rgba(0,229,160,0.15)" stroke="#00e5a0" stroke-width="2"/>
        <path d="M16 24l5.5 6L32 18" stroke="#00e5a0" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <div>
        <div class="brand">DocShield</div>
        <div class="tagline">Document Integrity Verification</div>
      </div>
    </div>

    <div class="url-banner">
      <span class="url-label">Verify URL</span>
      <span class="url-val" id="site-url">loading…</span>
      <button class="copy-btn" onclick="copyUrl()">Copy</button>
    </div>
  </header>

  <div class="tabs">
    <button class="tab active" onclick="switchTab('scan')">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><rect x="7" y="7" width="10" height="10" rx="1"/></svg>
      Scan QR
    </button>
    <button class="tab" onclick="switchTab('upload')">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      Upload & Verify
    </button>
    <button class="tab" onclick="switchTab('generate')">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
      Generate QR
    </button>
  </div>

  <!-- ── SCAN TAB ── -->
  <div class="panel active" id="panel-scan">
    <div class="card">
      <div class="card-title">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        Live Camera QR Scanner
      </div>
      <div class="scanner-wrap">
        <video id="qr-video" autoplay muted playsinline></video>
        <div class="scan-overlay">
          <div class="scan-frame"><span></span></div>
          <div class="scan-line"></div>
          <div class="scan-status" id="scan-status">Point camera at a DocShield QR code</div>
        </div>
      </div>
      <button class="btn btn-primary" id="start-btn" onclick="startScanner()">▶ Start Camera Scanner</button>
      <button class="btn btn-secondary" id="stop-btn" onclick="stopScanner()" style="display:none">⏹ Stop Camera</button>
      <p style="font-size:.72rem;color:rgba(245,240,232,.38);font-family:'Space Mono',monospace;margin-top:10px;line-height:1.6;text-align:center;">
        📱 <strong style="color:rgba(245,240,232,.55)">Android users:</strong> Camera requires HTTPS.
        If you see a certificate warning, tap <em>Advanced → Proceed</em> — this is expected for local self-signed certs.
      </p>

      <div style="margin-top:22px;border-top:1px solid var(--border);padding-top:20px;">
        <div class="card-title" style="margin-bottom:12px">Or Enter Document ID Manually</div>
        <div class="field-group">
          <label class="field-label">Document ID</label>
          <input class="field-input" id="manual-id" placeholder="e.g. DOC-2024-001 or full hash…">
        </div>
        <div class="field-group">
          <label class="field-label">Hash (optional — from QR URL)</label>
          <input class="field-input" id="manual-hash" placeholder="bound hash from QR…">
        </div>
        <button class="btn btn-primary" onclick="verifyById()">Verify Document ID</button>
      </div>
      <div id="scan-result" class="result-box"></div>
    </div>
    <div class="card">
      <div class="card-title">How Phone Scanning Works</div>
      <div class="steps">
        <div class="step"><div class="step-num">01 — SCAN</div><div class="step-title">Point Any Phone Camera</div><div class="step-desc">Google Lens, iPhone Camera, Samsung Camera — they all detect the URL in the QR and open this page automatically.</div></div>
        <div class="step"><div class="step-num">02 — REDIRECT</div><div class="step-title">Auto-Verification</div><div class="step-desc">The QR encodes <code style="color:var(--shield)">http://IP/?verify=ID&hash=HASH</code>. The page loads and instantly verifies the document.</div></div>
        <div class="step"><div class="step-num">03 — RESULT</div><div class="step-title">Instant Verdict</div><div class="step-desc">AUTHENTIC or FORGED — with full document metadata from the registry.</div></div>
      </div>
    </div>
  </div>

  <!-- ── UPLOAD TAB ── -->
  <div class="panel" id="panel-upload">
    <div class="card">
      <div class="card-title">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        Upload Full Document for Verification
      </div>
      <p style="font-size:.8rem;color:rgba(245,240,232,.5);margin-bottom:16px;line-height:1.6;">
        Upload the entire document. DocShield will scan the full image for an embedded QR stamp, extract the document ID, verify the hash, and compare the visual content against the original on file.
      </p>
      <div class="toggle-row">
        <div class="toggle" id="phys-toggle" onclick="togglePhysical()"></div>
        <span class="toggle-label" id="phys-label">Digital document mode</span>
      </div>
      <div class="drop-zone" id="drop-zone">
        <input type="file" id="file-input" accept=".pdf,.png,.jpg,.jpeg,.bmp,.tiff" onchange="handleFile(event)">
        <span class="drop-icon">📄</span>
        <div class="drop-title">Drop full document here or click to browse</div>
        <div class="drop-sub">PDF · PNG · JPG · BMP · TIFF</div>
      </div>
      <div class="preview-wrap" id="preview-wrap">
        <img id="preview-img" src="" alt="Document preview">
      </div>
      <button class="btn btn-primary" id="verify-btn" onclick="verifyUpload()" disabled>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        Verify Full Document
      </button>
      <button class="btn btn-secondary" id="clear-btn" onclick="clearUpload()" style="display:none">Clear</button>
      <div id="upload-result" class="result-box"></div>
    </div>
  </div>

  <!-- ── GENERATE TAB ── -->
  <div class="panel" id="panel-generate">
    <div class="card">
      <div class="card-title">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
        Generate Verification QR Code
      </div>
      <p style="font-size:.8rem;color:rgba(245,240,232,.5);margin-bottom:18px;line-height:1.6;">
        Generate QR codes from existing registry entries. The QR will embed a direct URL to this page — any phone scanner will redirect here for instant verification.
      </p>
      <div class="field-group">
        <label class="field-label">Document ID</label>
        <input class="field-input" id="gen-id" placeholder="Enter registered document ID…" oninput="scheduleQR()">
      </div>
      <div id="qr-placeholder">ENTER A DOCUMENT ID TO GENERATE QR</div>
      <div id="qr-output-wrap" style="display:none">
        <div class="qr-output-row">
          <div class="qr-canvas-box">
            <canvas id="qr-canvas" width="200" height="200"></canvas>
          </div>
          <div class="qr-info">
            <div class="qr-info-row"><span class="qi-key">URL</span><span class="qi-val" id="qi-url">—</span></div>
            <div class="qr-info-row"><span class="qi-key">Doc ID</span><span class="qi-val" id="qi-id">—</span></div>
            <div class="qr-info-row"><span class="qi-key">Holder</span><span class="qi-val" id="qi-holder">—</span></div>
            <div class="qr-info-row"><span class="qi-key">Type</span><span class="qi-val" id="qi-type">—</span></div>
            <div class="qr-info-row"><span class="qi-key">Issued</span><span class="qi-val" id="qi-issued">—</span></div>
            <button class="btn btn-primary" onclick="downloadQR()" style="margin-top:14px">
              ⬇ Download QR PNG
            </button>
          </div>
        </div>
      </div>
      <div id="gen-result" class="result-box"></div>
    </div>
    <div class="card">
      <div class="card-title">Phone Scanner Flow</div>
      <div class="steps">
        <div class="step"><div class="step-num">01 — EMBED</div><div class="step-title">URL in QR</div><div class="step-desc">The QR encodes <code style="color:var(--shield)">http://YOUR-IP:5000/?verify=ID&hash=HASH</code> so any default scanner opens verification.</div></div>
        <div class="step"><div class="step-num">02 — STAMP</div><div class="step-title">Print on Doc</div><div class="step-desc">Download the QR PNG and stamp it on your document corner using the Python issuer tool or any image editor.</div></div>
        <div class="step"><div class="step-num">03 — SCAN</div><div class="step-title">Works Everywhere</div><div class="step-desc">Google Lens, Apple Camera, Samsung Camera — all default scanners read URLs from QR codes and open the browser.</div></div>
      </div>
    </div>
  </div>

</div>
<footer>DocShield · SHA-256 + Perceptual Hash · Running locally on Python Flask</footer>
<div class="toast" id="toast"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/jsqr/1.4.0/jsQR.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
const BASE = window.location.origin;
let scanStream = null, scanInterval = null;
let uploadedFile = null, physicalMode = false;
let qrTimer = null;

// ── Init ──
window.addEventListener('DOMContentLoaded', () => {
  document.getElementById('site-url').textContent = window.location.origin + '/';

  const p = new URLSearchParams(window.location.search);
  const vid = p.get('verify'), vhash = p.get('hash');
  if (vid) {
    switchTab('scan');
    document.getElementById('manual-id').value = vid;
    if (vhash) document.getElementById('manual-hash').value = vhash;
    setTimeout(verifyById, 700);
  }
});

function switchTab(t) {
  document.querySelectorAll('.tab').forEach((el,i) => el.classList.toggle('active', ['scan','upload','generate'][i]===t));
  document.querySelectorAll('.panel').forEach(el => el.classList.remove('active'));
  document.getElementById('panel-'+t).classList.add('active');
  if (t !== 'scan') stopScanner();
}

// ── CAMERA SCANNER ──
async function startScanner() {
  const statusEl = document.getElementById('scan-status');
  const resultEl = 'scan-result';

  // Android Chrome requires HTTPS for getUserMedia on non-localhost origins.
  // If we're on HTTP and NOT localhost, warn the user.
  if (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
    showResult(resultEl, 'fake', 'HTTPS Required',
      'Android Chrome blocks camera access on plain HTTP. ' +
      'Please open <strong>https://' + location.hostname + ':' + location.port + '</strong> instead. ' +
      'Your browser will warn about a self-signed certificate — tap <em>Advanced → Proceed</em> to continue.', 0);
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showResult(resultEl, 'fake', 'Camera Not Supported',
      'Your browser does not support camera access. Use Chrome or Firefox on Android.', 0);
    return;
  }

  statusEl.textContent = 'Requesting camera permission…';

  // Try rear (environment) camera first, fall back to any camera
  const constraints = [
    { video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } },
    { video: { facingMode: 'environment' } },
    { video: true }
  ];

  let stream = null;
  let lastErr = null;
  for (const c of constraints) {
    try {
      stream = await navigator.mediaDevices.getUserMedia(c);
      break;
    } catch(e) {
      lastErr = e;
    }
  }

  if (!stream) {
    let msg = lastErr ? lastErr.message : 'Unknown error';
    if (lastErr && (lastErr.name === 'NotAllowedError' || lastErr.name === 'PermissionDeniedError')) {
      msg = 'Camera permission denied. Please allow camera access in your browser settings, then reload.';
    } else if (lastErr && lastErr.name === 'NotFoundError') {
      msg = 'No camera found on this device.';
    } else if (lastErr && lastErr.name === 'NotReadableError') {
      msg = 'Camera is in use by another app. Close other camera apps and try again.';
    }
    showResult(resultEl, 'fake', 'Camera Error', msg, 0);
    statusEl.textContent = 'Camera failed — see result below';
    return;
  }

  scanStream = stream;
  const v = document.getElementById('qr-video');
  v.srcObject = stream;
  try { await v.play(); } catch(e) {}
  document.getElementById('start-btn').style.display = 'none';
  document.getElementById('stop-btn').style.display = 'flex';
  statusEl.textContent = 'Scanning… point at a QR code';
  scanInterval = setInterval(() => scanFrame(v), 250);
}
function stopScanner() {
  if (scanStream) { scanStream.getTracks().forEach(t=>t.stop()); scanStream=null; }
  if (scanInterval) { clearInterval(scanInterval); scanInterval=null; }
  document.getElementById('start-btn').style.display='flex';
  document.getElementById('stop-btn').style.display='none';
  document.getElementById('scan-status').textContent='Camera stopped';
}
function scanFrame(v) {
  if (!v.videoWidth) return;
  const c=document.createElement('canvas'); c.width=v.videoWidth; c.height=v.videoHeight;
  const ctx=c.getContext('2d'); ctx.drawImage(v,0,0);
  const id=ctx.getImageData(0,0,c.width,c.height);
  const code=jsQR(id.data,id.width,id.height,{inversionAttempts:'dontInvert'});
  if (code) {
    stopScanner();
    document.getElementById('scan-status').textContent='✓ QR detected! Analyzing full document…';
    // Capture full document frame for pixel matching (vs forged copies with copied QR)
    c.toBlob(blob=>processQRWithDocument(code.data, blob, 'scan-result'), 'image/jpeg', 0.92);
  }
}
function processQR(data, rid) {
  let docId=data, hash='';
  try {
    const u=new URL(data); const p=new URLSearchParams(u.search);
    if(p.get('verify')) docId=p.get('verify');
    if(p.get('hash')) hash=p.get('hash');
  } catch(e) {
    try { const j=JSON.parse(data); docId=j.doc_id||data; hash=j.hash||''; } catch(e2){}
  }
  showLoading(rid, 'Verifying…');
  fetch(`${BASE}/api/verify-id`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({doc_id:docId, hash:hash})
  }).then(r=>r.json()).then(d=>renderResult(rid,d)).catch(e=>showResult(rid,'fake','API Error',e.message,0));
}
async function processQRWithDocument(qrData, docBlob, rid) {
  let docId=qrData, hash='';
  try {
    const u=new URL(qrData); const p=new URLSearchParams(u.search);
    if(p.get('verify')) docId=p.get('verify');
    if(p.get('hash')) hash=p.get('hash');
  } catch(e) {
    try { const j=JSON.parse(qrData); docId=j.doc_id||qrData; hash=j.hash||''; } catch(e2){}
  }
  showLoading(rid, 'Verifying with pixel matching…');
  const fd=new FormData();
  fd.append('file', docBlob, 'document.jpg');
  fd.append('doc_id', docId);
  fd.append('hash', hash);
  fd.append('from_camera', '1');
  try {
    const res=await fetch(`${BASE}/api/verify-with-image`, {method:'POST', body:fd});
    renderResult(rid, await res.json());
  } catch(e) {
    showResult(rid, 'fake', 'API Error', e.message, 0);
  }
}
async function verifyById() {
  const id=document.getElementById('manual-id').value.trim();
  const h=document.getElementById('manual-hash').value.trim();
  if (!id) { showToast('Enter a document ID first'); return; }
  showLoading('scan-result','Verifying…');
  const res = await fetch(`${BASE}/api/verify-id`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({doc_id:id, hash:h})
  });
  renderResult('scan-result', await res.json());
}

// ── UPLOAD & VERIFY (full document) ──
function togglePhysical() {
  physicalMode=!physicalMode;
  document.getElementById('phys-toggle').classList.toggle('on',physicalMode);
  document.getElementById('phys-label').textContent=physicalMode?'Physical document mode (photo)':'Digital document mode';
}
function handleFile(e) {
  const f=e.target.files[0]; if(!f) return;
  uploadedFile=f;
  if (f.type.startsWith('image/')) {
    const url=URL.createObjectURL(f);
    document.getElementById('preview-img').src=url;
    document.getElementById('preview-wrap').classList.add('show');
  } else {
    document.getElementById('preview-wrap').classList.remove('show');
  }
  document.getElementById('verify-btn').disabled=false;
  document.getElementById('clear-btn').style.display='flex';
  document.getElementById('upload-result').classList.remove('show');
  document.querySelector('.drop-title').textContent=f.name;
  document.querySelector('.drop-sub').textContent=`${(f.size/1024).toFixed(1)} KB · ${f.type||'unknown'}`;
}
const dz=document.getElementById('drop-zone');
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('drag-over')});
dz.addEventListener('dragleave',()=>dz.classList.remove('drag-over'));
dz.addEventListener('drop',e=>{
  e.preventDefault();dz.classList.remove('drag-over');
  const f=e.dataTransfer.files[0];
  if(f){document.getElementById('file-input').files=e.dataTransfer.files;handleFile({target:{files:[f]}});}
});
async function verifyUpload() {
  if (!uploadedFile) return;
  showLoading('upload-result','Scanning full document…');
  const fd=new FormData();
  fd.append('file', uploadedFile);
  fd.append('physical', physicalMode ? '1' : '0');
  try {
    const res=await fetch(`${BASE}/api/verify-upload`,{method:'POST',body:fd});
    renderResult('upload-result', await res.json());
  } catch(e) {
    showResult('upload-result','fake','Upload Error',e.message,0);
  }
}
function clearUpload() {
  uploadedFile=null;
  document.getElementById('file-input').value='';
  document.getElementById('preview-wrap').classList.remove('show');
  document.getElementById('verify-btn').disabled=true;
  document.getElementById('clear-btn').style.display='none';
  document.getElementById('upload-result').classList.remove('show');
  document.querySelector('.drop-title').textContent='Drop full document here or click to browse';
  document.querySelector('.drop-sub').textContent='PDF · PNG · JPG · BMP · TIFF';
}

// ── GENERATE QR ──
function scheduleQR() { clearTimeout(qrTimer); qrTimer=setTimeout(_generateQR, 500); }
async function _generateQR() {
  const id=document.getElementById('gen-id').value.trim();
  const ph=document.getElementById('qr-placeholder');
  const ow=document.getElementById('qr-output-wrap');
  const gr=document.getElementById('gen-result');
  if (!id) { ph.style.display='block'; ow.style.display='none'; gr.classList.remove('show'); return; }

  // Lookup in registry via API
  const res=await fetch(`${BASE}/api/doc-info?id=${encodeURIComponent(id)}`);
  const data=await res.json();

  if (!data.found) {
    ph.style.display='none'; ow.style.display='none';
    showResult('gen-result','fake','NOT IN REGISTRY',`Document ID "${id}" not found. Issue it first using the Python GUI.`,0);
    return;
  }

  gr.classList.remove('show');
  ph.style.display='none';
  ow.style.display='block';

  const verifyUrl=data.verify_url;
  document.getElementById('qi-url').textContent=verifyUrl.length>55?verifyUrl.substring(0,55)+'…':verifyUrl;
  document.getElementById('qi-id').textContent=id;
  document.getElementById('qi-holder').textContent=data.holder||'—';
  document.getElementById('qi-type').textContent=data.doc_type||'—';
  document.getElementById('qi-issued').textContent=data.issue_date||'—';

  // Draw QR
  const canvas=document.getElementById('qr-canvas');
  const ctx=canvas.getContext('2d');
  ctx.clearRect(0,0,200,200);
  const tmp=document.createElement('div');
  try {
    new QRCode(tmp,{text:verifyUrl,width:200,height:200,colorDark:'#0a0a0f',colorLight:'#ffffff',correctLevel:QRCode.CorrectLevel.H});
    setTimeout(()=>{
      const img=tmp.querySelector('img')||tmp.querySelector('canvas');
      if(img){
        if(img.tagName==='CANVAS'){ctx.drawImage(img,0,0,200,200);}
        else{const i=new Image();i.onload=()=>ctx.drawImage(i,0,0,200,200);i.src=img.src;}
      }
    },120);
  } catch(e){}
}
function downloadQR() {
  const canvas=document.getElementById('qr-canvas');
  const a=document.createElement('a');
  a.download=`docshield-qr-${document.getElementById('gen-id').value.trim()}.png`;
  a.href=canvas.toDataURL('image/png'); a.click();
  showToast('QR downloaded!');
}

// ── RESULT RENDERING ──
function renderResult(rid, data) {
  const doc=data.document||{};
  const lines=[
    data.verdict?`<strong>Verdict:</strong> ${data.verdict}`:'',
    doc.doc_id?`<strong>Document ID:</strong> ${doc.doc_id}`:'',
    doc.holder_name?`<strong>Holder:</strong> ${doc.holder_name}`:'',
    doc.doc_type?`<strong>Type:</strong> ${doc.doc_type}`:'',
    doc.issue_date?`<strong>Issued:</strong> ${doc.issue_date}`:'',
    doc.expiry_date?`<strong>Expires:</strong> ${doc.expiry_date}`:'',
    `<strong>Message:</strong> ${data.message||''}`,
  ].filter(Boolean).join('<br>');
  showResult(rid, data.valid?'legit':'fake',
    data.valid?'✓ AUTHENTIC':'✗ '+data.verdict, lines, data.confidence||0);
}
function showLoading(id,msg) {
  const b=document.getElementById(id);
  b.className='result-box loading show';
  b.innerHTML=`<div class="verdict"><div class="spinner"></div> ${msg}</div><div class="detail">Please wait…</div>`;
}
function showResult(id,type,verdict,detail,conf) {
  const b=document.getElementById(id);
  b.className=`result-box ${type} show`;
  const pct=Math.round((conf||0)*100);
  b.innerHTML=`
    <div class="verdict">${type==='legit'?'🛡️':'⚠️'} ${verdict}</div>
    <div class="detail">${detail}</div>
    <div class="conf-row">
      <span class="conf-lbl">Confidence</span>
      <div class="conf-bg"><div class="conf-fill ${conf<0.5?'low':''}" style="width:0%"></div></div>
      <span class="conf-pct">${pct}%</span>
    </div>`;
  requestAnimationFrame(()=>setTimeout(()=>{
    const f=b.querySelector('.conf-fill'); if(f) f.style.width=pct+'%';
  },50));
}
function copyUrl() { navigator.clipboard.writeText(window.location.origin+'/').then(()=>showToast('URL copied!')); }
function showToast(msg) {
  const t=document.getElementById('toast'); t.textContent=msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2800);
}
</script>
</body>
</html>"""


@flask_app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")


@flask_app.route("/api/verify-id", methods=["POST"])
def api_verify_id():
    """Verify by document ID + optional hash. Used by QR scans and manual entry."""
    data = request.get_json()
    doc_id = (data.get("doc_id") or "").strip()
    qr_hash = (data.get("hash") or "").strip()

    if not doc_id:
        return jsonify({"valid": False, "verdict": "NO ID", "message": "No document ID provided.", "document": None, "confidence": 0.0})

    result = verify_by_id_only(doc_id, qr_hash or None)
    return jsonify(result)


@flask_app.route("/api/verify-upload", methods=["POST"])
def api_verify_upload():
    """Verify a full uploaded document file. Scans entire document for QR + visual match."""
    if "file" not in request.files:
        return jsonify({"valid": False, "verdict": "NO FILE", "message": "No file uploaded.", "document": None, "confidence": 0.0})

    f = request.files["file"]
    is_physical = request.form.get("physical", "0") == "1"

    try:
        file_bytes = f.read()
        pil_img = pil_from_bytes(file_bytes, f.filename)
        result = verify_document_full(pil_img, is_physical=is_physical)
        return jsonify(result)
    except Exception as e:
        return jsonify({"valid": False, "verdict": "ERROR", "message": str(e), "document": None, "confidence": 0.0})


@flask_app.route("/api/verify-with-image", methods=["POST"])
def api_verify_with_image():
    """Verify QR + captured document image for pixel matching (detects forged copies with copied QR codes)."""
    if "file" not in request.files:
        return jsonify({"valid": False, "verdict": "NO IMAGE", "message": "No image captured.", "document": None, "confidence": 0.0})

    f = request.files["file"]
    doc_id = request.form.get("doc_id", "").strip()
    qr_hash = request.form.get("hash", "").strip()
    from_camera = request.form.get("from_camera", "0") == "1"

    if not doc_id:
        return jsonify({"valid": False, "verdict": "NO ID", "message": "Document ID not extracted.", "document": None, "confidence": 0.0})

    try:
        file_bytes = f.read()
        pil_img = pil_from_bytes(file_bytes, f.filename)
        
        # Lookup document in registry
        doc = lookup_doc_by_id(doc_id)
        if not doc:
            return jsonify({"valid": False, "verdict": "NOT IN REGISTRY", 
                           "message": f"Document ID '{doc_id}' not found. May be forged.",
                           "document": None, "confidence": 0.0})

        # Hash verification
        if qr_hash:
            expected_hash = make_bound_hash(
                doc["doc_id"], doc["holder_name"],
                doc["doc_type"], doc["issue_date"],
                doc.get("file_hash", "")
            )
            if qr_hash != expected_hash:
                return jsonify({"valid": False, "verdict": "TAMPERED QR",
                               "message": "QR code does not match registry. This document has been tampered with or has a forged QR.",
                               "document": doc, "confidence": 0.0})

        # ≈ Pixel matching for camera captures ≈
        # Test if the captured image matches the stored visual hash
        if doc.get("visual_hash") or doc.get("perceptual_hash"):
            captured_vhash = visual_fingerprint(pil_img)
            stored_vhash = doc.get("visual_hash", "")
            
            if stored_vhash and captured_vhash == stored_vhash:
                # Perfect pixel match — very high confidence for digital documents
                return jsonify({"valid": True, "verdict": "AUTHENTIC",
                               "message": "QR verified + pixels match original. Document is AUTHENTIC.",
                               "document": doc, "confidence": 1.0})
            
            # For photos (physical documents), use perceptual hash + content matching
            if doc.get("perceptual_hash"):
                phash = perceptual_hash(pil_img)
                phash_score = compare_perceptual_hash(doc["perceptual_hash"], phash)
                
                text_score = 0.5
                if doc.get("text_features"):
                    text_feat = extract_text_features(pil_img)
                    text_score = compare_text_features(doc["text_features"], text_feat)
                
                confidence = phash_score * 0.7 + text_score * 0.3
                
                if confidence >= 0.65:
                    return jsonify({"valid": True, "verdict": "AUTHENTIC",
                                   "message": f"QR + perceptual match. Document is GENUINE ({confidence:.0%} match).",
                                   "document": doc, "confidence": confidence})
                else:
                    return jsonify({"valid": False, "verdict": "CONTENT MISMATCH",
                                   "message": f"QR is valid but content doesn't match ({confidence:.0%} similarity). Possible FORGERY or substitution.",
                                   "document": doc, "confidence": confidence})
        
        # Fallback — QR verified but no visual hash stored
        return jsonify({"valid": True, "verdict": "AUTHENTIC",
                       "message": "QR code verified successfully. Document matches registry.",
                       "document": doc, "confidence": 0.95})

    except Exception as e:
        return jsonify({"valid": False, "verdict": "ERROR", "message": str(e), "document": None, "confidence": 0.0})


@flask_app.route("/api/doc-info")
def api_doc_info():
    """Return metadata + verify URL for a doc ID (used by QR generator)."""
    doc_id = request.args.get("id", "").strip()
    if not doc_id:
        return jsonify({"found": False})

    doc = lookup_doc_by_id(doc_id)
    if doc:
        verify_url = doc.get("verify_url") or build_verify_url(doc_id, doc.get("hash", ""))
        return jsonify({
            "found": True,
            "holder": doc.get("holder_name"),
            "doc_type": doc.get("doc_type"),
            "issue_date": doc.get("issue_date"),
            "verify_url": verify_url,
        })
    return jsonify({"found": False})


@flask_app.route("/api/registry")
def api_registry():
    """Return full registry summary (for debugging)."""
    records = load_registry()
    summary = []
    for rec in records:
        doc = rec.get("document", {})
        summary.append({
            "doc_id": doc.get("doc_id"),
            "holder": doc.get("holder_name"),
            "type": doc.get("doc_type"),
            "issued": rec.get("issued_date"),
        })
    db_backend = "supabase_postgres" if (_get_conn() is not None) else "local_json_fallback"
    return jsonify({"count": len(summary), "records": summary, "backend": db_backend})


@flask_app.route("/admin")
def admin_dashboard():
    """Admin dashboard — full registry access and verification controls."""
    records = load_registry()
    db_status = "✅ PostgreSQL" if _get_conn() else "📄 JSON Fallback"
    
    rows_html = ""
    for rec in records:
        doc = rec.get("document", {})
        rows_html += f"""
        <tr>
            <td>{doc.get('doc_id', 'N/A')}</td>
            <td>{doc.get('holder_name', 'N/A')}</td>
            <td>{doc.get('doc_type', 'N/A')}</td>
            <td>{doc.get('issue_date', 'N/A')}</td>
            <td>{rec.get('issued_date', 'N/A')[:10]}</td>
            <td><button class="btn-sm" onclick="copyToClipboard('{doc.get('doc_id')}')">📋 Copy ID</button></td>
        </tr>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>DocShield Admin Dashboard</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: #0f1419; color: #f5f0e8; }}
            .header {{ background: #1A237E; padding: 20px; text-align: center; }}
            .header h1 {{ font-size: 28px; font-weight: bold; }}
            .header p {{ font-size: 13px; opacity: 0.7; margin-top: 5px; }}
            .container {{ max-width: 1400px; margin: 20px auto; padding: 0 20px; }}
            .status {{ background: #1B5E20; border-radius: 6px; padding: 12px 16px; margin-bottom: 20px; font-weight: bold; }}
            .status.postgres {{ color: #A5D6A7; }}
            .status.json {{ color: #FFCC80; }}
            table {{ width: 100%; border-collapse: collapse; background: #1a1f2e; border-radius: 8px; overflow: hidden; }}
            th {{ background: #263238; padding: 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #37474F; }}
            td {{ padding: 12px; border-bottom: 1px solid #37474F; }}
            tr:hover {{ background: #252d3d; }}
            .btn-sm {{ padding: 6px 12px; background: #2C3E50; border: 1px solid #34495E; color: #fff; border-radius: 4px; cursor: pointer; font-size: 12px; }}
            .btn-sm:hover {{ background: #34495E; }}
            footer {{ text-align: center; padding: 20px; font-size: 12px; opacity: 0.5; margin-top: 40px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🛡️ DocShield Admin Dashboard</h1>
            <p>Full registry access and verification management</p>
        </div>
        <div class="container">
            <div class="status {'postgres' if _get_conn() else 'json'}">📂 Database: {db_status} | Total Documents: {len(records)}</div>
            <table>
                <thead>
                    <tr>
                        <th>Document ID</th>
                        <th>Holder Name</th>
                        <th>Type</th>
                        <th>Issue Date</th>
                        <th>Recorded</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
        <footer>DocShield | Secure document verification system</footer>
        <script>
            function copyToClipboard(text) {{
                navigator.clipboard.writeText(text).then(() => alert('Copied: ' + text));
            }}
        </script>
    </body>
    </html>
    """


def run_flask():
    """Run Flask in a daemon thread over HTTPS (required for Android camera access)."""
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)  # Suppress request logs

    # Try HTTPS first (required for getUserMedia on Android Chrome)
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(CERT_FILE, KEY_FILE)
            flask_app.run(host="0.0.0.0", port=WEB_PORT, debug=False,
                          use_reloader=False, ssl_context=ctx)
            return
        except Exception as e:
            print(f"  [SSL] HTTPS failed ({e}), falling back to HTTP.")

    # Fallback: plain HTTP (camera won't work on Android for non-localhost)
    flask_app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)


# =============================================================================
# RESULT PANEL (GUI) - Only loaded in local environments with tkinter
# =============================================================================

if TKINTER_AVAILABLE:
    class ResultPanel(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg="#f0f0f0", relief="solid", borderwidth=2)
        self.IDLE = "#f0f0f0"
        self.OK   = "#90EE90"
        self.FAIL = "#F08080"
        self.WARN = "#FFE5B4"
        self.title = tk.Label(self, text="VERIFICATION RESULT", font=("Arial", 14, "bold"), bg=self.IDLE)
        self.title.pack(pady=(10, 5))
        self.verdict = tk.Label(self, text="Waiting for verification…", font=("Arial", 20, "bold"), bg=self.IDLE, height=2)
        self.verdict.pack(pady=5, padx=10)
        self.confidence = tk.Label(self, text="", font=("Arial", 11), bg=self.IDLE)
        self.confidence.pack()
        self.details = tk.Label(self, text="", font=("Arial", 10), bg=self.IDLE, justify="left", wraplength=600)
        self.details.pack(pady=10, padx=15)
        self.reset()

    def show_legit(self, doc: dict, confidence: float = 1.0):
        self.configure(bg=self.OK)
        self.title.configure(bg=self.OK, text="✅  AUTHENTIC DOCUMENT  ✅")
        self.verdict.configure(bg=self.OK, text="✓  LEGITIMATE  ✓", fg="darkgreen")
        self.confidence.configure(bg=self.OK, text=f"Confidence: {confidence:.1%}" if confidence < 1.0 else "")
        lines = [
            f"Document ID: {doc.get('doc_id','N/A')}",
            f"Holder: {doc.get('holder_name','N/A')}",
            f"Type: {doc.get('doc_type','N/A')}",
            f"Issued: {doc.get('issue_date','N/A')}",
        ]
        if doc.get("expiry_date"):
            lines.append(f"Expires: {doc['expiry_date']}")
        self.details.configure(bg=self.OK, text="\n".join(lines), fg="darkgreen")

    def show_fake(self, reason: str, confidence: float = 0.0):
        self.configure(bg=self.FAIL)
        self.title.configure(bg=self.FAIL, text="❌  FORGED / INVALID  ❌")
        self.verdict.configure(bg=self.FAIL, text="✗  FAKE  ✗", fg="darkred")
        self.confidence.configure(bg=self.FAIL, text=f"Confidence: {confidence:.1%}" if confidence > 0 else "")
        self.details.configure(bg=self.FAIL, text=reason, fg="darkred")

    def reset(self):
        self.configure(bg=self.IDLE)
        self.title.configure(bg=self.IDLE, text="VERIFICATION RESULT")
        self.verdict.configure(bg=self.IDLE, text="Waiting for verification…", fg="black")
        self.confidence.configure(bg=self.IDLE, text="")
        self.details.configure(bg=self.IDLE, text="")


# =============================================================================
# ISSUE TAB (GUI)
# =============================================================================

class IssueTab(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self._src_path = None
        self._file_hash = None
        self._build()

    def _build(self):
        title_frame = tk.Frame(self, bg="#E8F5E9")
        title_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(title_frame, text="📄  Issue New Secured Document",
                 font=("Arial", 14, "bold"), bg="#E8F5E9", fg="#1B5E20").pack(pady=5)

        file_frame = tk.LabelFrame(self, text="Step 1: Select Document", padx=10, pady=10)
        file_frame.pack(fill="x", padx=10, pady=5)
        self.file_label = tk.Label(file_frame, text="No file selected", fg="red")
        self.file_label.pack(anchor="w")
        btn_frame = tk.Frame(file_frame)
        btn_frame.pack(fill="x", pady=5)
        tk.Button(btn_frame, text="📎  Browse Document", command=self._browse,
                  bg="#37474F", fg="white", width=20).pack(side="left", padx=5)
        tk.Button(btn_frame, text="✖  Clear", command=self._clear,
                  bg="#B0BEC5", width=10).pack(side="left", padx=5)
        self.hash_label = tk.Label(file_frame, text="", font=("Courier", 8))
        self.hash_label.pack(anchor="w")

        details_frame = tk.LabelFrame(self, text="Step 2: Document Details", padx=10, pady=10)
        details_frame.pack(fill="x", padx=10, pady=5)
        row = 0
        for label, attr in [("Document ID:*", "doc_id"), ("Holder Name:*", "holder"),
                              ("Issue Date:*", "issue_date")]:
            tk.Label(details_frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = tk.Entry(details_frame, width=40)
            entry.grid(row=row, column=1, pady=2, padx=5)
            setattr(self, attr, entry)
            row += 1
        self.issue_date.insert(0, datetime.now().strftime("%Y-%m-%d"))

        tk.Label(details_frame, text="Document Type:*").grid(row=row, column=0, sticky="w", pady=2)
        self.doc_type = ttk.Combobox(details_frame, width=38, values=[
            "Admission Letter", "Certificate", "Degree", "ID Card",
            "Passport", "Contract", "Title Deed", "Other"])
        self.doc_type.grid(row=row, column=1, pady=2, padx=5)
        row += 1

        tk.Label(details_frame, text="Expiry Date:").grid(row=row, column=0, sticky="w", pady=2)
        self.expiry = tk.Entry(details_frame, width=40)
        self.expiry.grid(row=row, column=1, pady=2, padx=5)
        row += 1

        tk.Label(details_frame, text="Additional Notes:").grid(row=row, column=0, sticky="nw", pady=2)
        self.notes = tk.Text(details_frame, height=3, width=40)
        self.notes.grid(row=row, column=1, pady=2, padx=5)

        tk.Button(self, text="🔐  Step 3: Generate Secured Document",
                  command=self._generate, bg="#1B5E20", fg="white",
                  font=("Arial", 12, "bold"), height=2).pack(fill="x", padx=10, pady=10)
        self.status = tk.Label(self, text="", fg="#1B5E20", wraplength=600)
        self.status.pack(pady=5)
        preview_frame = tk.LabelFrame(self, text="Preview")
        preview_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.preview = tk.Label(preview_frame, bg="#f0f0f0", height=10)
        self.preview.pack(fill="both", expand=True)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select document to secure",
            filetypes=[("All supported", "*.pdf *.png *.jpg *.jpeg *.bmp *.tiff"),
                       ("PDF files", "*.pdf"), ("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff")])
        if path:
            try:
                with open(path, "rb") as f:
                    self._file_hash = hash_bytes(f.read())
                self._src_path = path
                self.file_label.config(text=f"Selected: {os.path.basename(path)}", fg="green")
                self.hash_label.config(text=f"Hash: {self._file_hash[:50]}…")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to read file: {e}")

    def _clear(self):
        self._src_path = None
        self._file_hash = None
        self.file_label.config(text="No file selected", fg="red")
        self.hash_label.config(text="")

    def _generate(self):
        if not self._src_path:
            messagebox.showerror("Error", "Please select a document first")
            return
        doc_id = self.doc_id.get().strip()
        holder = self.holder.get().strip()
        doc_type = self.doc_type.get().strip()
        issue_date = self.issue_date.get().strip()
        if not all([doc_id, holder, doc_type, issue_date]):
            messagebox.showerror("Error", "Please fill all required fields (*)")
            return
        ext = os.path.splitext(self._src_path)[1]
        out_path = filedialog.asksaveasfilename(
            title="Save secured document as", defaultextension=ext,
            filetypes=[(f"{ext.upper()} files", f"*{ext}")],
            initialfile=f"SECURED_{doc_id}{ext}")
        if not out_path:
            return
        self.status.config(text="Generating secured document…")
        self.update()
        threading.Thread(target=self._generate_thread,
                         args=(doc_id, holder, doc_type, issue_date,
                               self.expiry.get().strip(),
                               self.notes.get("1.0", tk.END).strip(), out_path),
                         daemon=True).start()

    def _generate_thread(self, doc_id, holder, doc_type, issue_date, expiry, notes, out_path):
        try:
            bound_hash = make_bound_hash(doc_id, holder, doc_type, issue_date, self._file_hash)
            # QR encodes the web verify URL — phones open browser on scan
            verify_url = build_verify_url(doc_id, bound_hash)
            qr = generate_qr_pil(verify_url)

            if out_path.lower().endswith('.pdf'):
                embed_qr_into_pdf(self._src_path, qr, out_path)
            else:
                embed_qr_into_image(self._src_path, qr, out_path)

            secured = file_to_pil(out_path)
            visual_hash = visual_fingerprint(secured)
            phash = perceptual_hash(secured)
            text_feat = extract_text_features(secured)

            doc = {
                "doc_id": doc_id, "holder_name": holder, "doc_type": doc_type,
                "issue_date": issue_date, "expiry_date": expiry, "additional": notes,
                "file_hash": self._file_hash, "visual_hash": visual_hash,
                "perceptual_hash": phash, "text_features": text_feat,
                "hash": bound_hash, "verify_url": verify_url,
                "timestamp": datetime.now().isoformat()
            }
            save_to_registry(doc)
            self.after(0, lambda: self._done(True, out_path, doc))
        except Exception as e:
            self.after(0, lambda: self._done(False, str(e), None))

    def _done(self, success, message, doc):
        if success:
            self.status.config(text=f"✅ Saved: {os.path.basename(message)}")
            verify_url = doc.get("verify_url", "")
            messagebox.showinfo("Success",
                f"Document issued!\n\n"
                f"ID: {doc['doc_id']}\nHolder: {doc['holder_name']}\n\n"
                f"QR encodes URL:\n{verify_url}\n\n"
                f"Phone cameras will auto-open the web verification page!")
            try:
                img = file_to_pil(message, dpi=100)
                img.thumbnail((500, 200))
                imgtk = ImageTk.PhotoImage(img)
                self.preview.config(image=imgtk)
                self.preview.image = imgtk
            except:
                pass
        else:
            self.status.config(text="❌ Generation failed")
            messagebox.showerror("Error", f"Failed: {message}")


# =============================================================================
# CAMERA TAB (GUI)
# =============================================================================

class CameraTab(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.camera = None
        self.running = False
        self.captured_frame = None
        self.captured_hash = None
        self.physical_mode = tk.BooleanVar(value=True)
        self._build()

    def _build(self):
        title_frame = tk.Frame(self, bg="#FFF9C4")
        title_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(title_frame, text="📷  Camera Verification",
                 font=("Arial", 14, "bold"), bg="#FFF9C4").pack(pady=5)

        controls = tk.Frame(self)
        controls.pack(fill="x", padx=10, pady=5)
        tk.Label(controls, text="Camera:").pack(side="left")
        self.cam_id = tk.StringVar(value="0")
        tk.Entry(controls, textvariable=self.cam_id, width=3).pack(side="left", padx=2)
        tk.Label(controls, text="Mode:").pack(side="left", padx=(10, 2))
        ttk.Checkbutton(controls, text="Physical Document", variable=self.physical_mode).pack(side="left")
        tk.Button(controls, text="▶ Start", command=self._start, bg="green", fg="white").pack(side="left", padx=5)
        tk.Button(controls, text="⏹ Stop", command=self._stop, bg="red", fg="white").pack(side="left", padx=5)

        feed_frame = tk.LabelFrame(self, text="Camera Feed")
        feed_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.feed = tk.Label(feed_frame, bg="black", height=15)
        self.feed.pack(fill="both", expand=True)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=5)
        self.capture_btn = tk.Button(btn_frame, text="📸 Capture Page",
                                     command=self._capture, bg="#1565C0", fg="white",
                                     font=("Arial", 10, "bold"), width=15, state="disabled")
        self.capture_btn.pack(side="left", padx=5)
        self.scan_btn = tk.Button(btn_frame, text="🔳 Scan QR",
                                  command=self._scan, bg="#6A1B9A", fg="white",
                                  font=("Arial", 10, "bold"), width=15, state="disabled")
        self.scan_btn.pack(side="left", padx=5)
        tk.Button(btn_frame, text="🔄 Reset", command=self._reset, bg="orange").pack(side="left", padx=5)

        self.cap_status = tk.Label(self, text="⚫ No page captured", font=("Arial", 9))
        self.cap_status.pack(anchor="w", padx=15)
        self.quality_status = tk.Label(self, text="", font=("Arial", 9))
        self.quality_status.pack(anchor="w", padx=15)
        self.result = ResultPanel(self)
        self.result.pack(fill="x", padx=10, pady=5)

    def _start(self):
        try:
            cam_id = int(self.cam_id.get())
        except:
            cam_id = 0
        self.camera = cv2.VideoCapture(cam_id)
        if not self.camera.isOpened():
            messagebox.showerror("Error", "Could not open camera")
            return
        self.running = True
        self.capture_btn.config(state="normal")
        self._update_feed()

    def _stop(self):
        self.running = False
        if self.camera:
            self.camera.release()
        self.feed.config(image="", text="Camera stopped", bg="black")
        self.capture_btn.config(state="disabled")
        self.scan_btn.config(state="disabled")

    def _capture(self):
        if not self.camera or not self.running:
            return
        ret, frame = self.camera.read()
        if ret:
            self.captured_frame = frame.copy()
            self.captured_hash = hash_image_array_camera(frame)
            if self.physical_mode.get():
                pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                quality_ok, msg = check_photo_quality(pil)
                self.quality_status.config(
                    text=f"{'✅' if quality_ok else '⚠'} {msg}",
                    fg="green" if quality_ok else "orange")
            self.cap_status.config(text=f"✅ Page captured: {self.captured_hash[:20]}…", fg="green")
            self.scan_btn.config(state="normal")

    def _scan(self):
        if not self.camera or not self.running or self.captured_hash is None:
            return
        ret, frame = self.camera.read()
        if not ret:
            return
        qr_str = extract_qr_from_array(frame)
        if not qr_str:
            messagebox.showwarning("No QR", "No QR code detected in frame")
            return

        # Parse QR (may be a URL or JSON)
        doc_id, qr_hash = None, None
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(qr_str)
            params = parse_qs(parsed.query)
            if "verify" in params:
                doc_id = params["verify"][0]
            if "hash" in params:
                qr_hash = params["hash"][0]
        except:
            pass
        if not doc_id:
            try:
                payload = json.loads(qr_str)
                doc_id = payload.get("doc_id", "")
                qr_hash = payload.get("hash", "")
            except:
                pass

        if not doc_id:
            self.result.show_fake("Could not parse QR code")
            return

        result = verify_by_id_only(doc_id, qr_hash)

        if result["valid"]:
            doc = result["document"]
            # Also verify visual content
            pil = Image.fromarray(cv2.cvtColor(self.captured_frame, cv2.COLOR_BGR2RGB))
            full = verify_document_full(pil, is_physical=self.physical_mode.get(),
                                        doc_id_hint=doc_id, hash_hint=qr_hash)
            if full["valid"]:
                self.result.show_legit(doc, full.get("confidence", 1.0))
            else:
                self.result.show_fake(full["message"], full.get("confidence", 0.0))
        else:
            self.result.show_fake(result["message"])

    def _reset(self):
        self.captured_frame = None
        self.captured_hash = None
        self.cap_status.config(text="⚫ No page captured")
        self.quality_status.config(text="")
        self.scan_btn.config(state="disabled")
        self.result.reset()

    def _update_feed(self):
        if not self.running or not self.camera:
            return
        ret, frame = self.camera.read()
        if ret:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            codes = decode(gray, symbols=[ZBarSymbol.QRCODE])
            if codes:
                for qr in codes:
                    pts = qr.polygon
                    if len(pts) > 4:
                        pts = cv2.convexHull(np.array(list(pts), dtype=np.float32))
                    for j in range(len(pts)):
                        cv2.line(frame, tuple(map(int, pts[j])),
                                 tuple(map(int, pts[(j + 1) % len(pts)])), (0, 255, 0), 2)
            if self.captured_hash:
                cv2.putText(frame, "Page captured ✓", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            mode = "PHYSICAL" if self.physical_mode.get() else "DIGITAL"
            cv2.putText(frame, f"Mode: {mode}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            img.thumbnail((600, 300))
            imgtk = ImageTk.PhotoImage(img)
            self.feed.config(image=imgtk)
            self.feed.image = imgtk
        self.after(50, self._update_feed)


# =============================================================================
# UPLOAD TAB (GUI)
# =============================================================================

class UploadTab(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self._path = None
        self.physical_mode = tk.BooleanVar(value=False)
        self._build()

    def _build(self):
        title_frame = tk.Frame(self, bg="#E3F2FD")
        title_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(title_frame, text="📂  Upload & Verify Document",
                 font=("Arial", 14, "bold"), bg="#E3F2FD", fg="#1A237E").pack(pady=5)

        file_frame = tk.LabelFrame(self, text="Select Document", padx=10, pady=10)
        file_frame.pack(fill="x", padx=10, pady=5)
        mode_frame = tk.Frame(file_frame)
        mode_frame.pack(fill="x", pady=5)
        tk.Checkbutton(mode_frame, text="📷 This is a photo of a physical document",
                       variable=self.physical_mode, font=("Arial", 10, "bold")).pack(side="left")
        self.file_label = tk.Label(file_frame, text="No file selected", fg="red")
        self.file_label.pack(anchor="w", pady=5)
        btn_frame = tk.Frame(file_frame)
        btn_frame.pack(fill="x")
        tk.Button(btn_frame, text="📂 Browse", command=self._browse,
                  bg="#1565C0", fg="white", width=15).pack(side="left", padx=5)
        tk.Button(btn_frame, text="🔍 Verify", command=self._verify,
                  bg="#B71C1C", fg="white", width=15).pack(side="left", padx=5)
        tk.Button(btn_frame, text="🔄 Clear", command=self._clear,
                  bg="#757575", fg="white", width=10).pack(side="left", padx=5)
        self.quality_label = tk.Label(file_frame, text="", fg="#555")
        self.quality_label.pack(anchor="w", pady=2)

        preview_frame = tk.LabelFrame(self, text="Document Preview")
        preview_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.preview_lbl = tk.Label(preview_frame, bg="#222", height=10)
        self.preview_lbl.pack(fill="both", expand=True)

        self.result = ResultPanel(self)
        self.result.pack(fill="x", padx=10, pady=5)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select document to verify",
            filetypes=[("All supported", "*.pdf *.png *.jpg *.jpeg *.bmp *.tiff"),
                       ("PDF files", "*.pdf"), ("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff")])
        if path:
            self._path = path
            self.file_label.config(text=f"Selected: {os.path.basename(path)}", fg="green")
            self.result.reset()
            self.quality_label.config(text="")
            try:
                img = file_to_pil(path, dpi=100)
                img.thumbnail((500, 200))
                imgtk = ImageTk.PhotoImage(img)
                self.preview_lbl.config(image=imgtk)
                self.preview_lbl.image = imgtk
            except Exception as e:
                self.preview_lbl.config(text=f"Preview error: {e}")

    def _clear(self):
        self._path = None
        self.file_label.config(text="No file selected", fg="red")
        self.preview_lbl.config(image="", text="")
        self.quality_label.config(text="")
        self.result.reset()

    def _verify(self):
        if not self._path:
            messagebox.showerror("Error", "Please select a document first")
            return
        self.result.reset()
        self.quality_label.config(text="Verifying…", fg="blue")
        self.update()
        threading.Thread(target=self._verify_thread, daemon=True).start()

    def _verify_thread(self):
        try:
            pil = file_to_pil(self._path, dpi=150)
            if self.physical_mode.get():
                quality_ok, quality_msg = check_photo_quality(pil)
                self.after(0, lambda: self.quality_label.config(
                    text=quality_msg, fg="green" if quality_ok else "orange"))
            result = verify_document_full(pil, is_physical=self.physical_mode.get())
            self.after(0, lambda: self._show(result))
        except Exception as e:
            self.after(0, lambda: self._show({"valid": False, "verdict": "ERROR",
                                              "message": str(e), "document": None, "confidence": 0.0}))

    def _show(self, result):
        if result["valid"]:
            self.result.show_legit(result["document"], result.get("confidence", 1.0))
        else:
            self.result.show_fake(result["message"], result.get("confidence", 0.0))


    # =============================================================================
    # MAIN APPLICATION (GUI)
    # =============================================================================

    class App:
        def __init__(self):
            self.root = tk.Tk()
            self.root.title("Document Security System  [Web: https://localhost:5443]")
            self.root.geometry("900x980")
            self.root.update_idletasks()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            x = (self.root.winfo_screenwidth() // 2) - (w // 2)
            y = (self.root.winfo_screenheight() // 2) - (h // 2)
            self.root.geometry(f"{w}x{h}+{x}+{y}")

            # Header
            header = tk.Frame(self.root, bg="#1A237E", height=60)
            header.pack(fill="x")
            header.pack_propagate(False)
            tk.Label(header, text="🔐  Document Security System  🔐",
                     font=("Arial", 18, "bold"), bg="#1A237E", fg="white").pack(expand=True)

            # Web server info bar
            info_frame = tk.Frame(self.root, bg="#1B5E20")
            info_frame.pack(fill="x")
            web_url = f"https://localhost:{WEB_PORT}"
            admin_url = f"https://localhost:{WEB_PORT}/admin"
            lan_url = f"https://{LOCAL_IP}:{WEB_PORT}"
            lan_admin = f"https://{LOCAL_IP}:{WEB_PORT}/admin"
            info_label = tk.Label(
                info_frame,
                text=f"🌐  Users: {web_url}  |  Admins: {admin_url}  (accept cert warning)",
                font=("Arial", 10, "bold"), bg="#1B5E20", fg="#A5D6A7", cursor="hand2")
            info_label.pack(pady=6)
            info_label.bind("<Button-1>", lambda e: webbrowser.open(web_url))

            # Registry info
            info2 = tk.Frame(self.root, bg="#E8EAF6")
            info2.pack(fill="x", padx=10, pady=3)
            db_status = "✅ Supabase PostgreSQL connected" if _get_conn() else "⚠ Using local JSON fallback"
            tk.Label(info2, text=f"Registry: {db_status}  |  {DATABASE_URL.split('@')[1].split('?')[0]}",
                     font=("Arial", 9), bg="#E8EAF6", fg="#1A237E").pack()

            # Notebook
            notebook = ttk.Notebook(self.root)
            notebook.pack(fill="both", expand=True, padx=10, pady=5)
            notebook.add(IssueTab(notebook), text="  📄 Issue Document  ")
            notebook.add(CameraTab(notebook), text="  📷 Camera Verify  ")
            notebook.add(UploadTab(notebook), text="  📂 Upload Verify  ")

            self.status = tk.Label(self.root, text=f"Ready  |  Web UI: https://localhost:{WEB_PORT}  |  Admin: https://localhost:{WEB_PORT}/admin", bd=1, relief="sunken", anchor="w")
            self.status.pack(fill="x", padx=10, pady=2)

        def run(self):
            self.root.mainloop()

else:
    # Stub for cloud environments without tkinter
    App = None


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("  DocShield — Document Security System")
    print("=" * 70)

    # Connect to PostgreSQL and ensure table exists
    print("  [DB] Connecting to PostgreSQL…")
    init_db()

    # Generate self-signed SSL cert if not present (needed for Android camera)
    ssl_ok = False
    if not (os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)):
        print("  [SSL] Generating self-signed certificate for HTTPS…")
        ssl_ok = generate_self_signed_cert(CERT_FILE, KEY_FILE, LOCAL_IP)
    else:
        ssl_ok = True
        print(f"  [SSL] Using existing cert: {CERT_FILE}")

    scheme = "https" if ssl_ok else "http"
    print(f"  Starting web server on port {WEB_PORT} ({scheme.upper()})…")
    print("=" * 70)
    print(f"  👥 USER INTERFACE (verification only):")
    print(f"      Local : {scheme}://localhost:{WEB_PORT}")
    print(f"      LAN   : {scheme}://{LOCAL_IP}:{WEB_PORT}")
    print(f"  ")
    print(f"  👨‍💼 ADMIN DASHBOARD (registry management):")
    print(f"      Local : {scheme}://localhost:{WEB_PORT}/admin")
    print(f"      LAN   : {scheme}://{LOCAL_IP}:{WEB_PORT}/admin")
    print("=" * 70)
    if ssl_ok:
        print(f"  ✓ Android: open the LAN URL on your phone")
        print(f"  ✓ Chrome will warn 'Not secure' → Advanced → Proceed (self-signed)")
    else:
        print(f"  ⚠ WARNING: Running HTTP. Android camera will NOT work on LAN.")
        print(f"  ⚠ Fix: pip install cryptography  then restart.")
    print("=" * 70)

    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(1.5)  # Give Flask a moment to bind

    # Open user interface in browser automatically (only if tkinter available)
    if TKINTER_AVAILABLE:
        webbrowser.open(f"{scheme}://localhost:{WEB_PORT}")
        # Launch tkinter GUI
        app = App()
        app.run()
    else:
        print("  [INFO] Cloud environment detected (no tkinter). Flask web server running.")
        print("  [INFO] Access the interface at the URLs above.")
        # Keep the Flask thread alive
        while True:
            time.sleep(1)