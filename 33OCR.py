# Code Hybrid - Unified & Modular (with Atomic Log Saving - PERSISTENT CLOUD LOG)
import os
import io
import re
import json
import time
import zipfile
import tempfile 
import base64
import streamlit as st
import platform
import os 
import traceback 

from PyPDF2 import PdfReader, PdfWriter
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload 
from googleapiclient.errors import HttpError

# Optional OCR libs (used only when OCR is selected)
from pdf2image import convert_from_bytes
import pytesseract

# --- Custom Safe Slugify Function (Standard Library Only) ---
def safe_slugify(value, separator='_'):
    """
    Converts a string to a safe slug format using only standard Python libraries.
    """
    value = str(value).strip().lower()
    value, _ = os.path.splitext(value)
    value = re.sub(r'[^\w\s-]', '', value)
    return re.sub(r'[-\s_]+', separator, value)
# -----------------------------------------------------------------

# Set tesseract path only on Windows
if platform.system() == "Windows":
    possible_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in possible_paths:
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break
    else:
        st.error("Tesseract not found on your system. Install from UB Mannheim release.")
# -----------------------------
# Persistent User Preferences (UNITS REMAINS UNCHANGED)
# -----------------------------
PREF_FILE = "user_prefs.json"

if "user_prefs" not in st.session_state:
    if os.path.exists(PREF_FILE):
        try:
            with open(PREF_FILE, "r") as f:
                st.session_state.user_prefs = json.load(f)
        except Exception:
            st.session_state.user_prefs = {}
    else:
        st.session_state.user_prefs = {}

_defaults = {
    "drive_folder": "your-default-folder-id",
    "enable_drive_upload": True,
    "enable_local_download": True,
    "naming_pattern": "{year} {month} {ippis}",
    "timezone": "Africa/Lagos",
    "date_format": "YYYY-MM-DD",
    "ocr_mode": "Hybrid"
}
for k, v in _defaults.items():
    st.session_state.user_prefs.setdefault(k, v)

# -----------------------------
# UI - Sidebar Settings (REMAINS UNCHANGED)
# -----------------------------
st.sidebar.header("⚙️ Settings")

st.session_state.user_prefs["drive_folder"] = st.sidebar.text_input(
    "Google Drive Folder ID",
    value=st.session_state.user_prefs["drive_folder"],
    key="drive_folder"
)

st.session_state.user_prefs["enable_drive_upload"] = st.sidebar.checkbox(
    "Enable Google Drive features",
    value=st.session_state.user_prefs["enable_drive_upload"],
    key="enable_drive_upload"
)

st.session_state.user_prefs["enable_local_download"] = st.sidebar.checkbox(
    "Enable local download (ZIP)",
    value=st.session_state.user_prefs["enable_local_download"],
    key="enable_local_download"
)

st.session_state.user_prefs["naming_pattern"] = st.sidebar.text_input(
    "File naming pattern",
    value=st.session_state.user_prefs["naming_pattern"],
    help="Use placeholders: {year}, {month}, {ippis}",
    key="naming_pattern"
)

st.session_state.user_prefs["ocr_mode"] = st.sidebar.selectbox(
    "OCR Mode",
    options=["Normal", "Hybrid", "Full OCR"],
    index=["Normal", "Hybrid", "Full OCR"].index(st.session_state.user_prefs.get("ocr_mode", "Hybrid")),
    help="Normal = PyPDF2 text only. Hybrid = text first, OCR fallback per-page. Full OCR = OCR every page.",
    key="ocr_mode"
)

try:
    with open(PREF_FILE, "w") as f:
        json.dump(st.session_state.user_prefs, f)
except Exception:
    st.warning("Could not save preferences to disk (permissions?). Preferences will persist only in this session.")

# -----------------------------
# Page config & styling (REMAINS UNCHANGED)
# -----------------------------
st.set_page_config(page_title="ARMTI Payslip Manager", page_icon="assets/ARMTI.png", layout="wide")

st.markdown("""
<style>
.app-title { font-family: 'Montserrat', sans-serif; color: #2E86C1; font-size: 2.2rem; font-weight:700; }
.top-banner { text-align: center; margin-bottom: 18px; }
.stButton button { background-color: #2E86C1; color: white; border-radius: 8px; padding: 0.5em 1em; font-weight:bold; }
.stButton button:hover { background-color: #1B4F72; }
</style>
""", unsafe_allow_html=True)

def get_base64_of_bin_file(bin_file):
    if not os.path.exists(bin_file):
        return ""
    with open(bin_file, "rb") as f:
        return base64.b64encode(f.read()).decode()

logo_base64 = get_base64_of_bin_file("assets/ARMTI.png")
st.markdown(
    f"""
    <div class="top-banner">
        {'<img src="data:image/png;base64,' + logo_base64 + '" width="110" style="margin-bottom:8px;">' if logo_base64 else ''}
        <h1 class="app-title">ARMTI PAYSLIP MANAGER</h1>
    </div>
    """,
    unsafe_allow_html=True
)

# -----------------------------
# Google Drive helpers (UPDATED for Log Persistence)
# -----------------------------
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']
LOG_FILE_NAME = "uploaded_files.json"

try:
    GOOGLE_DRIVE_FOLDER_ID = st.secrets["google_drive_folder_id"]
except Exception:
    GOOGLE_DRIVE_FOLDER_ID = st.session_state.user_prefs["drive_folder"]
    if not GOOGLE_DRIVE_FOLDER_ID:
        st.warning("No Google Drive folder configured. Set one in settings or in st.secrets.")

def authenticate_google_drive():
    try:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=SCOPES
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        st.error(f"Google Drive authentication failed: {e}")
        return None

def upload_file_to_google_drive(service, filename, file_path, mime_type="application/pdf"):
    """Uploads a file reading from a local path, enabling resumable upload."""
    f = None
    try:
        file_metadata = {"name": filename, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
        f = open(file_path, "rb")
        media = MediaIoBaseUpload(f, mimetype=mime_type, resumable=True) 
        file = service.files().create(body=file_metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
        return file.get("id")
    except HttpError as e:
        raise
    except Exception as e:
        raise
    finally:
        if f: f.close()

def get_file_id_by_name(service, file_name, folder_id):
    """Searches for a file by name within the specified folder and returns its ID."""
    query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
    try:
        response = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = response.get('files', [])
        return files[0]['id'] if files else None
    except Exception as e:
        st.error(f"Error searching for file {file_name} in Drive: {e}")
        return None

def update_or_create_log_file(service, log_data_list):
    """Updates the persistent log file on Google Drive or creates it if it doesn't exist."""
    
    if not GOOGLE_DRIVE_FOLDER_ID:
        st.error("Cannot save log: Google Drive Folder ID is not configured.")
        return
        
    log_content = json.dumps(log_data_list).encode('utf-8')
    log_stream = io.BytesIO(log_content)

    try:
        file_id = get_file_id_by_name(service, LOG_FILE_NAME, GOOGLE_DRIVE_FOLDER_ID)
        
        if file_id:
            # Update existing file
            media = MediaIoBaseUpload(log_stream, mimetype='application/json', resumable=True)
            service.files().update(
                fileId=file_id, 
                media_body=media, 
                fields='id',
                supportsAllDrives=True
            ).execute()
            # add_to_log(f"✅ Log file updated on Google Drive (ID: {file_id}).", "info") # Too chatty
        else:
            # Create new file
            file_metadata = {"name": LOG_FILE_NAME, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
            media = MediaIoBaseUpload(log_stream, mimetype='application/json')
            file = service.files().create(
                body=file_metadata, 
                media_body=media, 
                fields='id',
                supportsAllDrives=True
            ).execute()
            # add_to_log(f"✅ Log file created on Google Drive (ID: {file.get('id')}).", "info") # Too chatty

    except Exception as e:
        add_to_log(f"❌ CRITICAL: Failed to save log to Google Drive: {e}", "error")

def load_log_from_google_drive(service):
    """Downloads the log file from Google Drive at app startup."""
    if not service or not GOOGLE_DRIVE_FOLDER_ID: return set()
    
    try:
        file_id = get_file_id_by_name(service, LOG_FILE_NAME, GOOGLE_DRIVE_FOLDER_ID)
        if not file_id:
            add_to_log(f"Info: Persistent log file '{LOG_FILE_NAME}' not found on Google Drive. Starting fresh.", "info")
            return set()
            
        request = service.files().get_media(fileId=file_id)
        file_io = io.BytesIO()
        downloader = MediaIoBaseDownload(file_io, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        
        log_content = file_io.getvalue().decode('utf-8')
        log_list = json.loads(log_content)
        add_to_log(f"✅ Persistent log loaded from Google Drive. Found {len(log_list)} records.", "success")
        return set(log_list)

    except Exception as e:
        add_to_log(f"❌ CRITICAL: Failed to load log from Google Drive. Starting fresh. Error: {e}", "error")
        return set()

# -----------------------------
# Text/detail extraction utilities (REMAINS UNCHANGED)
# -----------------------------
def get_details_from_text(merged_text, group_idx=None, ocr_mode="Hybrid"):
    if not merged_text: return None
    month_map = {'JANUARY': '01', 'FEBRUARY': '02', 'MARCH': '03', 'APRIL': '04', 'MAY': '05', 'JUNE': '06','JULY': '07', 'AUGUST': '08', 'SEPTEMBER': '09', 'OCTOBER': '10', 'NOVEMBER': '11', 'DECEMBER': '12'}
    abbr_map = {'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06','JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'}
    year, month = None, None
    if ocr_mode == "Full OCR":
        full_date_pattern = r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b'
        match = re.search(full_date_pattern, merged_text, re.IGNORECASE)
        if match: month_name = match.group(1).upper(); month = month_map.get(month_name); year = match.group(2)
    else:
        month_abbr_match = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\-\s]?\s*(20\d{2})\b', merged_text, re.IGNORECASE)
        if month_abbr_match: abbr = month_abbr_match.group(1).upper(); month = abbr_map.get(abbr); year = month_abbr_match.group(2)
        if not month:
            full_date_pattern = r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b'
            match = re.search(full_date_pattern, merged_text, re.IGNORECASE)
            if match: month_name = match.group(1).upper(); month = month_map.get(month_name); year = match.group(2)
    ippis_match = re.search(r'\b(\d{6})\b', merged_text)
    ippis_number = ippis_match.group(1) if ippis_match else None
    if year and month and ippis_number: return {'year': year, 'month': month, 'ippis': ippis_number}
    return None

# -----------------------------
# Core text extraction helpers / Grouping / Splitting (REMAINS UNCHANGED)
# -----------------------------
def extract_text_from_pdf_non_ocr(reader):
    texts = []
    for page in reader.pages:
        try: texts.append(page.extract_text() or "")
        except Exception: texts.append("")
    return texts

def extract_text_page_ocr(pdf_bytes, page_index, dpi=120):
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=page_index+1, last_page=page_index+1)
        return pytesseract.image_to_string(pages[0]) if pages else ""
    except Exception as e:
        st.warning(f"OCR page {page_index+1} failed: {e}")
        return ""

def extract_all_pages_ocr(pdf_bytes, num_pages_total, dpi=120):
    ocr_texts = []
    bar = st.progress(0, text=f"Performing Full OCR on {num_pages_total} pages...")
    for i in range(num_pages_total):
        ocr_texts.append(extract_text_page_ocr(pdf_bytes, i, dpi=dpi))
        bar.progress((i+1)/num_pages_total, text=f"Full OCR: Page {i+1}/{num_pages_total}")
    bar.empty()
    return ocr_texts

def group_pages_by_payslip_from_texts(page_texts, pdf_num_pages):
    groups, current_group = [], []
    START_MARKER = "FEDERAL GOVERNMENT OF NIGERIA"
    for i, text in enumerate(page_texts):
        text_upper = (text or "").upper()
        if START_MARKER in text_upper:
            if current_group: groups.append(current_group)
            current_group = [i]
        elif current_group:
            current_group.append(i)
    if current_group: groups.append(current_group)
    if not groups or (len(groups) == 1 and len(groups[0]) == pdf_num_pages):
        st.info("No distinct payslip markers found. Treating each page as a separate payslip.")
        return [[i] for i in range(pdf_num_pages)]
    return groups

def split_and_rename_pdf_dynamic(input_pdf_bytes, ocr_mode="Hybrid", naming_pattern="{year} {month} {ippis}", original_file_prefix="Payslip"):
    """Splits PDF pages into payslips, extracts details, and saves to temp files."""
    processed = []
    try:
        reader = PdfReader(io.BytesIO(input_pdf_bytes))
        num_pages = len(reader.pages)
        if ocr_mode == "Full OCR": page_texts = extract_all_pages_ocr(input_pdf_bytes, num_pages)
        else:
            bar = st.progress(0, text=f"Extracting text from {original_file_prefix}...")
            non_ocr_texts = extract_text_from_pdf_non_ocr(reader)
            if ocr_mode == "Normal": page_texts = non_ocr_texts
            else:
                page_texts = []
                for i, txt in enumerate(non_ocr_texts):
                    candidate = txt or ""
                    if len(candidate.strip()) < 80 or not ("FEDERAL GOVERNMENT" in candidate.upper() or re.search(r'\b(20\d{2})\b', candidate)):
                        ocr_txt = extract_text_page_ocr(input_pdf_bytes, i)
                        page_texts.append(ocr_txt if len(ocr_txt.strip()) > len(candidate.strip()) else candidate)
                    else: page_texts.append(candidate)
                    bar.progress((i+1)/num_pages, text=f"Hybrid extraction (page {i+1}/{num_pages})")
            bar.empty()
        
        page_groups = group_pages_by_payslip_from_texts(page_texts, num_pages)
        bar = st.progress(0, text=f"Processing {len(page_groups)} payslips from {original_file_prefix}...")
        
        for g_idx, group in enumerate(page_groups, start=1):
            writer = PdfWriter()
            merged_text = "\n".join(page_texts[pg] or "" for pg in group)
            for pg in group: writer.add_page(reader.pages[pg])
            details = get_details_from_text(merged_text, g_idx, ocr_mode=ocr_mode)
            info = {'status': "Details Missing", 'selected_for_upload': False, 'original_file_name': original_file_prefix}
            
            if details:
                core_filename = naming_pattern.format(**details)
                info.update({
                    **details,
                    'key': f"{original_file_prefix}_{details['year']}_{details['month']}_{details['ippis']}_{g_idx}",
                    'filename': f"[{original_file_prefix}] {core_filename}.pdf",
                    'status': "Details Extracted", 'selected_for_upload': True})
            else:
                info.update({'key': f"{original_file_prefix}_no_details_group_{g_idx}", 'filename': f"[{original_file_prefix}] Payslip_Group_{g_idx}_missing_details.pdf"})

            buf = io.BytesIO(); writer.write(buf)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(buf.getvalue())
                info['temp_file_path'] = tmp.name
            processed.append(info)
            bar.progress(g_idx / len(page_groups), text=f"Processed payslip {g_idx}/{len(page_groups)}")
        bar.empty()
        return processed
        
    except Exception as e:
        st.error(f"Error while processing PDF '{original_file_prefix}': {e}")
        st.error(traceback.format_exc())
        return []

# -----------------------------
# Session & Log Management (UPDATED FOR CLOUD PERSISTENCE)
# -----------------------------
def add_to_log(message, status="info"):
    st.session_state.activity_log.insert(0, {"message": message, "status": status})

def cleanup_temp_files():
    """Removes temporary files associated with processed payslips to free disk space."""
    if 'processed_payslips_data' in st.session_state:
        paths_to_delete = [item.get('temp_file_path') for item in st.session_state.processed_payslips_data if item.get('temp_file_path')]
        for path in paths_to_delete:
            if os.path.exists(path):
                try: os.remove(path)
                except Exception as e: add_to_log(f"Failed to clean up temp file {path} during reset: {e}", "warning")
        
if 'processed_payslips_data' not in st.session_state: st.session_state.processed_payslips_data = []
if 'activity_log' not in st.session_state: st.session_state.activity_log = []
if 'new_file_uploaded' not in st.session_state: st.session_state.new_file_uploaded = False

# --- LOG INITIALIZATION (USES GOOGLE DRIVE) ---
if 'uploaded_file_keys_log' not in st.session_state:
    # Authenticate immediately to load the persistent log
    log_service = authenticate_google_drive()
    st.session_state.uploaded_file_keys_log = load_log_from_google_drive(log_service)
# ----------------------------------------------


# -----------------------------
# App Instructions & Uploader (REMAINS UNCHANGED)
# -----------------------------
st.markdown("""
Upload **multiple** multi-page PDFs containing payslips.
""")
uploaded_files = st.file_uploader("📂 Upload PDF files containing payslips", type="pdf", accept_multiple_files=True
