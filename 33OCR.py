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
from googleapiclient.errors import HttpError

# ===================== CONFIG =====================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
poppler_path = r"C:\Users\HP\Desktop\HDD\ARMTI\Payslip\poppler"
uploaded_log = "uploaded_files.json"

if not os.path.exists(uploaded_log):
    with open(uploaded_log, "w") as f:
        json.dump([], f)

# ===================== FUNCTIONS =====================
def extract_text_from_page(page):
    """Try extracting text directly; fallback to OCR."""
    text = page.extract_text()
    if not text or len(text.strip()) < 20:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            writer = PdfWriter()
            writer.add_page(page)
            writer.write(tmp_pdf)
            tmp_pdf_path = tmp_pdf.name
        images = convert_from_path(tmp_pdf_path, poppler_path=poppler_path)
        text = "\n".join(pytesseract.image_to_string(img) for img in images)
        os.remove(tmp_pdf_path)
    return text

def extract_ippis_and_period(text):
    """Extract period (YYYY_MM or MM/YYYY) and 6-digit IPPIS number."""
    ippis = None
    year, month = None, None

    ippis_match = re.search(r"\b(\d{6})\b", text)
    if ippis_match:
        ippis = ippis_match.group(1)

    ym_match = re.search(r"(20\d{2})[^\d]{0,3}(0?[1-9]|1[0-2])", text)
    if ym_match:
        year, month = ym_match.group(1), ym_match.group(2).zfill(2)
    else:
        my_match = re.search(r"(0?[1-9]|1[0-2])[^\d]{0,3}(20\d{2})", text)
        if my_match:
            month, year = my_match.group(1).zfill(2), my_match.group(2)

    return year, month, ippis

def split_and_process_pdf(pdf_path):
    """Split, name, and return list of processed file paths."""
    reader = PdfReader(pdf_path)
    processed_files = []

    for i, page in enumerate(reader.pages):
        text = extract_text_from_page(page)
        year, month, ippis = extract_ippis_and_period(text)
        if not (year and month and ippis):
            continue

        filename = f"{year}_{month}_{ippis}.pdf"
        writer = PdfWriter()
        writer.add_page(page)
        output_path = os.path.join(tempfile.gettempdir(), filename)
        with open(output_path, "wb") as f:
            writer.write(f)
        processed_files.append(output_path)
    return processed_files

def load_uploaded_log():
    with open(uploaded_log, "r") as f:
        return json.load(f)

def save_uploaded_log(log):
    with open(uploaded_log, "w") as f:
        json.dump(log, f, indent=2)

def upload_to_drive(service, folder_id, file_path):
    """Single non-resumable upload."""
    file_name = os.path.basename(file_path)
    metadata = {"name": file_name, "parents": [folder_id]}
    media = MediaFileUpload(file_path, mimetype="application/pdf", resumable=False)
    service.files().create(body=metadata, media_body=media, fields="id").execute()

def safe_upload_to_drive(service, folder_id, file_path, progress_bar, max_retries=3):
    """Robust upload with retry + progress safety."""
    uploaded_log_data = load_uploaded_log()
    key = os.path.basename(file_path).replace(".pdf", "")

    if key in uploaded_log_data:
        st.info(f"⏩ Skipped (Already uploaded): {key}")
        return True

    for attempt in range(max_retries):
        try:
            upload_to_drive(service, folder_id, file_path)
            uploaded_log_data.append(key)
            save_uploaded_log(uploaded_log_data)
            st.success(f"✅ Uploaded: {key}")
            return True

        except HttpError as e:
            status = e.resp.status if hasattr(e, "resp") else "Unknown"
            st.warning(f"⚠️ HTTP {status} error for {key} (attempt {attempt+1}/{max_retries}). Retrying...")
        except Exception as e:
            st.warning(f"⚠️ Upload failed for {key} (attempt {attempt+1}/{max_retries}): {e}")

        time.sleep(3)

    st.error(f"❌ Skipping {key} after {max_retries} failed attempts.")
    return False

def init_drive_service(sa_info):
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

# ===================== STREAMLIT UI =====================
st.set_page_config(page_title="Payslip Splitter & Uploader", layout="centered")
st.title("📄 Payslip Processor (Hybrid + Robust Upload)")
st.caption("Split, OCR, and upload payslips safely to Google Drive")

uploaded_pdf = st.file_uploader("📤 Upload PDF File", type=["pdf"])
sa_file = st.file_uploader("🔐 Upload Service Account JSON", type=["json"])
folder_id = st.text_input("📁 Enter Google Drive Folder ID")

if st.button("🚀 Process & Upload"):
    if not uploaded_pdf or not sa_file or not folder_id:
        st.error("Please upload the PDF, service account file, and provide the folder ID.")
    else:
        with st.spinner("Processing... Please wait."):
            temp_path = os.path.join(tempfile.gettempdir(), uploaded_pdf.name)
            with open(temp_path, "wb") as f:
                f.write(uploaded_pdf.getbuffer())

            processed_files = split_and_process_pdf(temp_path)
            st.success(f"✅ Processed {len(processed_files)} pages successfully.")

            sa_info = json.load(sa_file)
            service = init_drive_service(sa_info)

            st.write("🚚 Uploading files to Google Drive...")
            progress_bar = st.progress(0)
            total = len(processed_files)
            uploaded_count = 0

            for idx, file_path in enumerate(processed_files, start=1):
                safe_upload_to_drive(service, folder_id, file_path, progress_bar)
                uploaded_count += 1
                progress_bar.progress(int((uploaded_count / total) * 100))
                time.sleep(0.5)

            st.success(f"🎉 Completed! Uploaded {uploaded_count}/{total} files to Drive.")
