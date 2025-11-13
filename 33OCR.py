import os
import io
import re
import json
import time
import tempfile
import pytesseract
import streamlit as st
from pdf2image import convert_from_path
from PyPDF2 import PdfReader, PdfWriter
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ===================== CONFIG =====================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
poppler_path = r"C:\Users\HP\Desktop\HDD\ARMTI\Payslip\poppler"
uploaded_log = "uploaded_files.json"

# Ensure upload log exists
if not os.path.exists(uploaded_log):
    with open(uploaded_log, "w") as f:
        json.dump([], f)

# ===================== FUNCTIONS =====================
def extract_text_from_page(page):
    """Try extracting text directly; fallback to OCR."""
    try:
        text = page.extract_text()
    except Exception:
        text = ""

    # If non-OCR extraction is weak, do OCR fallback
    if not text or len(text.strip()) < 20:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            writer = PdfWriter()
            writer.add_page(page)
            writer.write(tmp_pdf)
            tmp_pdf_path = tmp_pdf.name
        try:
            images = convert_from_path(tmp_pdf_path, poppler_path=poppler_path)
            text = "\n".join(pytesseract.image_to_string(img) for img in images)
        except Exception as e:
            st.warning(f"OCR conversion failed for page: {e}")
            text = text or ""
        finally:
            try:
                os.remove(tmp_pdf_path)
            except Exception:
                pass
    return text

def extract_ippis_and_period(text):
    """Extract period (YYYY_MM or MM/YYYY) and 6-digit IPPIS number."""
    ippis = None
    year, month = None, None

    if not text:
        return year, month, ippis

    # 6-digit IPPIS strictly
    ippis_match = re.search(r"\b(\d{6})\b", text)
    if ippis_match:
        ippis = ippis_match.group(1)

    # Period detection (two common patterns)
    ym_match = re.search(r"(20\d{2})[^\d]{0,3}(0?[1-9]|1[0-2])", text)
    if ym_match:
        year, month = ym_match.group(1), ym_match.group(2).zfill(2)
    else:
        my_match = re.search(r"(0?[1-9]|1[0-2])[^\d]{0,3}(20\d{2})", text)
        if my_match:
            month, year = my_match.group(1).zfill(2), my_match.group(2)

    return year, month, ippis

def split_and_process_pdf(pdf_path):
    """Split, name, and return list of processed file paths (temporary files)."""
    reader = PdfReader(pdf_path)
    processed_files = []

    for i, page in enumerate(reader.pages):
        text = extract_text_from_page(page)
        year, month, ippis = extract_ippis_and_period(text)
        if not (year and month and ippis):
            # Skip pages that don't meet the required extraction rules
            continue

        filename = f"{year}_{month}_{ippis}.pdf"
        writer = PdfWriter()
        writer.add_page(page)
        output_path = os.path.join(tempfile.gettempdir(), filename)

        # If a file with same name exists in temp dir, overwrite it to ensure latest bytes
        try:
            with open(output_path, "wb") as f:
                writer.write(f)
            processed_files.append(output_path)
        except Exception as e:
            st.warning(f"Failed to write temporary file {output_path}: {e}")

    return processed_files

def load_uploaded_log():
    """Return upload log as a set for O(1) membership checks."""
    try:
        with open(uploaded_log, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
            else:
                # repair corrupted log by returning empty set
                return set()
    except Exception:
        return set()

def save_uploaded_log(log_set):
    """Persist upload log (set -> list) to file."""
    try:
        with open(uploaded_log, "w") as f:
            json.dump(sorted(list(log_set)), f, indent=2)
    except Exception as e:
        st.warning(f"Could not save upload log: {e}")

def upload_to_drive(service, folder_id, file_path):
    """Non-resumable upload to Drive. Returns uploaded file ID on success."""
    file_name = os.path.basename(file_path)
    metadata = {"name": file_name}
    if folder_id:
        metadata["parents"] = [folder_id]
    media = MediaFileUpload(file_path, mimetype="application/pdf", resumable=False)
    file = service.files().create(body=metadata, media_body=media, fields="id").execute()
    return file.get("id")

def safe_upload_to_drive(service, folder_id, file_path, max_retries=3, wait_between=2, status_placeholder=None):
    """
    Robust upload with retries. Returns tuple (success: bool, details: str).
    - retries up to max_retries
    - waits wait_between seconds between attempts
    - updates status_placeholder (st.empty()) if provided
    - logs success in uploaded_files.json
    """
    uploaded_keys = load_uploaded_log()
    key = os.path.basename(file_path).replace(".pdf", "")

    # Skip already-logged uploads
    if key in uploaded_keys:
        msg = f"⏩ Skipped (Already uploaded): {key}"
        if status_placeholder:
            status_placeholder.info(msg)
        return True, msg

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            if status_placeholder:
                status_placeholder.info(f"Uploading {key} (attempt {attempt}/{max_retries})...")
            file_id = upload_to_drive(service, folder_id, file_path)
            uploaded_keys.add(key)
            save_uploaded_log(uploaded_keys)
            msg = f"✅ Uploaded: {key} (ID: {file_id})"
            if status_placeholder:
                status_placeholder.success(msg)
            return True, msg
        except Exception as e:
            last_exc = e
            # Update UI with warning and sleep
            if status_placeholder:
                status_placeholder.warning(f"⚠️ Upload failed for {key} (attempt {attempt}/{max_retries}): {e}")
            time.sleep(wait_between)
            continue

    # If we reach here, all attempts failed
    err_msg = f"❌ Skipping {key} after {max_retries} failed attempts. Last error: {last_exc}"
    if status_placeholder:
        status_placeholder.error(err_msg)
    return False, err_msg

def init_drive_service(sa_info):
    """Initialize Google Drive service from service account JSON data (dict)."""
    try:
        creds = service_account.Credentials.from_service_account_info(sa_info, scopes=["https://www.googleapis.com/auth/drive"])
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return service
    except Exception as e:
        st.error(f"Google Drive authentication failed: {e}")
        return None

# ===================== STREAMLIT UI =====================
st.set_page_config(page_title="Payslip Splitter & Uploader", layout="centered")
st.title("📄 Payslip Processor (Hybrid + Robust Drive Upload)")
st.caption("Split, OCR, and upload payslips safely to Google Drive")

uploaded_pdf = st.file_uploader("📤 Upload PDF File", type=["pdf"])
sa_file = st.file_uploader("🔐 Upload Service Account JSON (optional, can be provided for upload)", type=["json"])
folder_id = st.text_input("📁 Enter Google Drive Folder ID (optional, required for upload)")

# Options
retries_input = st.number_input("Max upload retries", min_value=1, max_value=10, value=3, step=1)
wait_seconds = st.number_input("Seconds between retries", min_value=1, max_value=10, value=2, step=1)

if st.button("🚀 Process & Upload"):
    if not uploaded_pdf:
        st.error("Please upload a PDF to process.")
    else:
        with st.spinner("Processing PDF — extracting pages and details..."):
            # write uploaded PDF to temp path
            temp_path = os.path.join(tempfile.gettempdir(), uploaded_pdf.name)
            try:
                with open(temp_path, "wb") as f:
                    f.write(uploaded_pdf.getbuffer())
            except Exception as e:
                st.error(f"Failed to write uploaded PDF to temp file: {e}")
                temp_path = None

            if not temp_path:
                st.stop()

            processed_files = split_and_process_pdf(temp_path)
            if not processed_files:
                st.warning("No valid payslip pages found (must contain Year, Month and a 6-digit IPPIS).")
            else:
                st.success(f"✅ Processed {len(processed_files)} payslip page(s) successfully.")

        # If user provided service account JSON and folder ID, proceed to upload
        if sa_file and folder_id:
            try:
                sa_info = json.load(sa_file)
            except Exception as e:
                st.error(f"Failed to read service account JSON: {e}")
                sa_info = None

            if not sa_info:
                st.stop()

            service = init_drive_service(sa_info)
            if not service:
                st.stop()

            st.write("🚚 Uploading processed files to Google Drive...")
            overall_progress = st.progress(0)
            total = len(processed_files)
            completed = 0

            # Create a visual status area for per-file updates
            status_boxes = [st.empty() for _ in range(total)]

            for idx, file_path in enumerate(processed_files):
                status_box = status_boxes[idx]
                status_box.info(f"Preparing upload for: {os.path.basename(file_path)}")

            # Start uploading each file, but don't let one failure block the rest
            for idx, file_path in enumerate(processed_files):
                status_box = status_boxes[idx]
                status_box.info(f"Starting upload: {os.path.basename(file_path)}")

                success, message = safe_upload_to_drive(
                    service=service,
                    folder_id=folder_id,
                    file_path=file_path,
                    max_retries=int(retries_input),
                    wait_between=int(wait_seconds),
                    status_placeholder=status_box
                )

                completed += 1
                overall_progress.progress(int((completed / total) * 100))

            overall_progress.empty()
            st.success(f"Uploads complete. Attempted {len(processed_files)} files. Check per-file statuses above.")
        else:
            st.info("Upload skipped — provide Service Account JSON and Google Drive Folder ID to upload.")
