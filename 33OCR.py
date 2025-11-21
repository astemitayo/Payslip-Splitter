# Code Hybrid - Unified & Modular (with Atomic Log Saving)
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
import traceback # Added for clearer error logging

from PyPDF2 import PdfReader, PdfWriter
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload 
from googleapiclient.errors import HttpError

# Optional OCR libs (used only when OCR is selected)
from pdf2image import convert_from_bytes
import pytesseract

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
else:
    # Linux (Streamlit Cloud) — do NOT set the path
    pass

# -----------------------------
# Persistent User Preferences
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
# UI - Sidebar Settings
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
# Page config & styling
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
# Google Drive helpers (Modified for file path input)
# -----------------------------
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']

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

# MODIFIED: Accepts file_path instead of file_bytes to reduce memory usage
def upload_file_to_google_drive(service, filename, file_path, mime_type="application/pdf"):
    """Uploads a file reading from a local path, enabling resumable upload."""
    f = None
    try:
        file_metadata = {"name": filename, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
        
        # Open file from disk for upload
        f = open(file_path, "rb")
        # Added resumable=True for better network failure handling
        media = MediaIoBaseUpload(f, mimetype=mime_type, resumable=True) 
        
        file = service.files().create(body=file_metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
        return file.get("id")
    except HttpError as e:
        # Re-raise specific HttpErrors to allow the retry loop to handle them
        raise
    except Exception as e:
        raise
    finally:
        if f: f.close()

# -----------------------------
# Text/detail extraction utilities (FINAL VERSION)
# -----------------------------
def get_details_from_text(merged_text, group_idx=None, ocr_mode="Hybrid"):
    if not merged_text:
        return None

    month_map = {
        'JANUARY': '01', 'FEBRUARY': '02', 'MARCH': '03', 'APRIL': '04', 'MAY': '05', 'JUNE': '06',
        'JULY': '07', 'AUGUST': '08', 'SEPTEMBER': '09', 'OCTOBER': '10', 'NOVEMBER': '11', 'DECEMBER': '12'
    }
    abbr_map = {
        'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
        'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
    }

    year, month = None, None

    # --- MODE-SPECIFIC LOGIC ---
    if ocr_mode == "Full OCR":
        # STRICT MODE: Use a single, atomic regex to find "MONTH YYYY" together.
        full_date_pattern = r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b'
        match = re.search(full_date_pattern, merged_text, re.IGNORECASE)
        if match:
            month_name = match.group(1).upper()
            month = month_map.get(month_name)
            year = match.group(2)

    else:
        # FLEXIBLE MODE: For Normal/Hybrid, trust abbreviations first for accuracy.
        month_abbr_match = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\-\s]?\s*(20\d{2})\b', merged_text, re.IGNORECASE)
        if month_abbr_match:
            abbr = month_abbr_match.group(1).upper()
            month = abbr_map.get(abbr)
            year = month_abbr_match.group(2)
        
        # Fallback to full month name if abbreviation not found
        if not month:
            full_date_pattern = r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b'
            match = re.search(full_date_pattern, merged_text, re.IGNORECASE)
            if match:
                month_name = match.group(1).upper()
                month = month_map.get(month_name)
                year = match.group(2)

    # IPPIS extraction is the same for all modes.
    ippis_match = re.search(r'\b(\d{6})\b', merged_text)
    ippis_number = ippis_match.group(1) if ippis_match else None

    if year and month and ippis_number:
        return {'year': year, 'month': month, 'ippis': ippis_number}
    
    return None

# -----------------------------
# Core text extraction helpers
# -----------------------------
def extract_text_from_pdf_non_ocr(reader):
    texts = []
    for page in reader.pages:
        try: texts.append(page.extract_text() or "")
        except Exception: texts.append("")
    return texts

def extract_text_page_ocr(pdf_bytes, page_index, dpi=120):
    try:
        # Ensure we are not passing an excessively high DPI to pdf2image
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

# -----------------------------
# Grouping function (Robust Version)
# -----------------------------
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

# -----------------------------
# Unified splitting function (Modified to save to tempfile)
# -----------------------------
def split_and_rename_pdf_dynamic(input_pdf_bytes, ocr_mode="Hybrid", naming_pattern="{year} {month} {ippis}"):
    processed = []
    try:
        reader = PdfReader(io.BytesIO(input_pdf_bytes))
        num_pages = len(reader.pages)
        if ocr_mode == "Full OCR": page_texts = extract_all_pages_ocr(input_pdf_bytes, num_pages)
        else:
            bar = st.progress(0, text="Extracting text...")
            non_ocr_texts = extract_text_from_pdf_non_ocr(reader)
            if ocr_mode == "Normal": page_texts = non_ocr_texts
            else:
                page_texts = []
                for i, txt in enumerate(non_ocr_texts):
                    candidate = txt or ""
                    if len(candidate.strip()) < 80 or not ("FEDERAL GOVERNMENT" in candidate.upper() or re.search(r'\b(20\d{2})\b', candidate)):
                        # If extraction is poor or non-existent, run OCR as fallback
                        ocr_txt = extract_text_page_ocr(input_pdf_bytes, i)
                        page_texts.append(ocr_txt if len(ocr_txt.strip()) > len(candidate.strip()) else candidate)
                    else: page_texts.append(candidate)
                    bar.progress((i+1)/num_pages, text=f"Hybrid extraction (page {i+1}/{num_pages})")
            bar.empty()
        
        page_groups = group_pages_by_payslip_from_texts(page_texts, num_pages)
        bar = st.progress(0, text=f"Processing {len(page_groups)} payslips...")
        for g_idx, group in enumerate(page_groups, start=1):
            writer = PdfWriter()
            merged_text = "\n".join(page_texts[pg] or "" for pg in group)
            for pg in group: writer.add_page(reader.pages[pg])
            
            details = get_details_from_text(merged_text, g_idx, ocr_mode=ocr_mode)
            
            info = {'status': "Details Missing", 'selected_for_upload': False}
            if details:
                info.update({
                    **details,
                    'key': f"{details['year']}_{details['month']}_{details['ippis']}_{g_idx}",
                    'filename': naming_pattern.format(**details) + ".pdf",
                    'status': "Details Extracted", 'selected_for_upload': True})
            else:
                info.update({
                    'key': f"no_details_group_{g_idx}",
                    'filename': f"Payslip_Group_{g_idx}_missing_details.pdf"})

            # --- START MEMORY-SAVING CHANGE ---
            # Save the split PDF to a temporary file and store the path
            buf = io.BytesIO()
            writer.write(buf)
            
            # Use delete=False because Streamlit keeps the temp files until script rerun/session end.
            # We explicitly clean them up in cleanup_temp_files().
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(buf.getvalue())
                info['temp_file_path'] = tmp.name
                
            # Removed: info['file_bytes'] = buf.getvalue() - This caused high memory usage
            # --- END MEMORY-SAVING CHANGE ---
            
            processed.append(info)
            bar.progress(g_idx / len(page_groups), text=f"Processed payslip {g_idx}/{len(page_groups)}")
        bar.empty()
        st.success("✅ All pages processed successfully!")
        return processed
    except Exception as e:
        st.error(f"Error while processing PDF: {e}")
        st.error(traceback.format_exc())
        return []

# -----------------------------
# Session & Log Management (Added Temp File Cleanup)
# -----------------------------
UPLOAD_LOG = "uploaded_files.json"

def add_to_log(message, status="info"):
    st.session_state.activity_log.insert(0, {"message": message, "status": status})

def cleanup_temp_files():
    """Removes temporary files associated with processed payslips to free disk space."""
    if 'processed_payslips_data' in st.session_state:
        for item in st.session_state.processed_payslips_data:
            path = item.get('temp_file_path')
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    # Log cleanup failure but don't stop the app
                    add_to_log(f"Failed to clean up temp file {path} during reset: {e}", "warning")
        # Do NOT clear st.session_state.processed_payslips_data here. It will be cleared 
        # when a new file is successfully uploaded/processed.

if 'processed_payslips_data' not in st.session_state: st.session_state.processed_payslips_data = []
if 'activity_log' not in st.session_state: st.session_state.activity_log = []
if 'new_file_uploaded' not in st.session_state: st.session_state.new_file_uploaded = False
if 'uploaded_file_keys_log' not in st.session_state:
    st.session_state.uploaded_file_keys_log = set()
    if os.path.exists(UPLOAD_LOG):
        try:
            with open(UPLOAD_LOG, "r") as f:
                st.session_state.uploaded_file_keys_log = set(json.load(f))
        except Exception: pass

# -----------------------------
# App Instructions & Uploader (MOVED UP FOR DEFINITION)
# -----------------------------
st.markdown("""
Upload a multi-page PDF containing payslips. The app can:
- split single-page payslips,
- group multi-page payslips (detected via markers),
- use OCR (full/hybrid) for scanned PDFs,
- allow review and selective upload to Google Drive and/or provide ZIP downloads.
""")
# --- VARIABLE DEFINITION IS HERE ---
uploaded_file = st.file_uploader("📂 Upload a PDF containing payslips", type="pdf", help="Drag & drop or click to browse")
# -----------------------------------


# New logic for uploaded file reset and cleanup (Now works as uploaded_file is defined)
if uploaded_file and not st.session_state.new_file_uploaded:
    # A new file has just been uploaded (uploaded_file is True, but new_file_uploaded is False)
    cleanup_temp_files() # Clear old data and temp files
    st.session_state.processed_payslips_data = [] # Clear the displayed data now
    st.session_state.activity_log = []
    st.session_state.new_file_uploaded = True
elif not uploaded_file and st.session_state.new_file_uploaded:
    # File has been cleared by the user (uploaded_file is None, but new_file_uploaded was True)
    cleanup_temp_files() 
    st.session_state.processed_payslips_data = [] # Clear the displayed data
    st.session_state.new_file_uploaded = False


if uploaded_file:
    if st.button("🚀 Split & Process Payslips", key="process_button"):
        
        # Explicitly clear any stale log/data just before processing
        cleanup_temp_files() 
        st.session_state.processed_payslips_data = [] 
        st.session_state.activity_log = []

        st.session_state.processed_payslips_data = split_and_rename_pdf_dynamic(
            uploaded_file.getvalue(),
            ocr_mode=st.session_state.user_prefs.get("ocr_mode", "Hybrid"),
            naming_pattern=st.session_state.user_prefs.get("naming_pattern", "{year} {month} {ippis}"))
        
        for item in st.session_state.processed_payslips_data:
            # New condition for failed upload status persistence
            if item.get('key') in st.session_state.uploaded_file_keys_log:
                item['upload_status_detail'] = 'Already uploaded'
                item['selected_for_upload'] = False
            elif item['status'] != "Details Extracted":
                item['upload_status_detail'] = 'Pending'
                item['selected_for_upload'] = False
            else:
                # Default for newly processed valid files
                item['upload_status_detail'] = 'Pending'


# -----------------------------
# Review & Actions UI
# -----------------------------
if st.session_state.processed_payslips_data:
    st.markdown("---")
    st.subheader("📊 Review & Select Payslips")
    
    # Check for any items marked 'Final Failure (Retry needed)' and re-select them automatically
    for item in st.session_state.processed_payslips_data:
        if item.get('upload_status_detail') == 'Final Failure (Retry needed)':
            item['selected_for_upload'] = True
    
    col_sel_all, col_desel_all = st.columns(2)
    if col_sel_all.button("✅ Select All Valid for Upload / Retry", key="select_all"):
        for item in st.session_state.processed_payslips_data:
            # Select all that are not already uploaded and have details
            if item.get('upload_status_detail') != 'Already uploaded' and item['status'] == 'Details Extracted':
                item['selected_for_upload'] = True
    if col_desel_all.button("❌ Deselect All for Upload", key="deselect_all"):
        for item in st.session_state.processed_payslips_data: item['selected_for_upload'] = False

    display_data = [{"Selected": item['selected_for_upload'], "Filename": item.get('filename'), "Year": item.get('year', '-'), "Month": item.get('month', '-'), "IPPIS": item.get('ippis', '-'), "Processing Status": item.get('status'), "Upload Status": item.get('upload_status_detail')} for item in st.session_state.processed_payslips_data]
    edited_data = st.data_editor(display_data, column_config={"Selected": st.column_config.CheckboxColumn("Upload?", help="Select to upload")}, hide_index=True, key="payslip_editor")
    for i, row in enumerate(edited_data): st.session_state.processed_payslips_data[i]['selected_for_upload'] = row['Selected']

    tab_drive, tab_download = st.tabs(["☁️ Google Drive Actions", "💻 Local Download"])

    with tab_drive:
        if st.session_state.user_prefs.get("enable_drive_upload", True):
            selected = [item for item in st.session_state.processed_payslips_data if item['selected_for_upload']]
            st.info(f"You have **{len(selected)}** payslips selected for Google Drive upload/retry.")
            
            # --- START UPLOAD RESILIENCE CHANGES ---
            if st.button("⬆️ Upload Selected to Google Drive", key="upload_button", disabled=not selected):
                service = authenticate_google_drive()
                if service:
                    st.session_state.activity_log = []
                    total_to_upload = len(selected)
                    add_to_log(f"Starting upload of {total_to_upload} files...")
                    log_placeholder = st.empty()
                    # Increased retries and setup for exponential backoff
                    MAX_RETRIES, RETRY_DELAY = 5, 2 
                    
                    for idx, item in enumerate(selected):
                        progress_prefix = f"({idx + 1}/{total_to_upload})"
                        
                        # Update log visibility
                        with log_placeholder.expander("Live Activity Log", expanded=True):
                            for log in st.session_state.activity_log:
                                if log['status'] == 'success': st.success(log['message'])
                                elif log['status'] == 'error': st.error(log['message'])
                                elif log['status'] == 'warning': st.warning(log['message'])
                                else: st.info(log['message'])
                                
                        uploaded = False
                        
                        # Check for the existence of the temporary file before attempting upload
                        file_path = item.get('temp_file_path')
                        if not file_path or not os.path.exists(file_path):
                            item['upload_status_detail'] = "Failed: File missing from disk"
                            add_to_log(f"{progress_prefix} ❌ Critical: Payslip file not found on disk for '{item['filename']}'. Skipping.", "error")
                            # Keep selected=True and status=Final Failure to signal to user they need to re-process the PDF
                            item['selected_for_upload'] = True
                            item['upload_status_detail'] = "Final Failure (Missing File)" 
                            continue # Skip this item
                            
                        for attempt in range(1, MAX_RETRIES + 1):
                            try:
                                add_to_log(f"{progress_prefix} 🚀 Uploading '{item['filename']}' (Attempt {attempt})...")
                                
                                # Pass the file path to the modified upload function
                                file_id = upload_file_to_google_drive(service, item['filename'], file_path)
                                
                                item['upload_status_detail'] = f"Uploaded (ID: {file_id})"
                                item['selected_for_upload'] = False # Deselect once successfully uploaded
                                st.session_state.uploaded_file_keys_log.add(item['key'])
                                try:
                                    with open(UPLOAD_LOG, "w") as f:
                                        json.dump(list(st.session_state.uploaded_file_keys_log), f)
                                except Exception as e:
                                    add_to_log(f"CRITICAL: Could not save upload log to disk! {e}", "error")
                                add_to_log(f"{progress_prefix} ✅ Success: '{item['filename']}'.", status="success")
                                uploaded = True
                                # After successful upload, remove the temp file
                                try:
                                    os.remove(file_path) 
                                    del item['temp_file_path']
                                except Exception as e:
                                    add_to_log(f"Warning: Failed to delete temp file {file_path}. {e}", "warning")
                                break 
                                
                            except Exception as e:
                                # Exponential Backoff Calculation
                                wait_time = RETRY_DELAY * (2 ** (attempt - 1))
                                if wait_time > 30: wait_time = 30 # Cap wait time at 30 seconds
                                
                                if attempt < MAX_RETRIES:
                                    add_to_log(f"{progress_prefix} ⚠️ Failed attempt {attempt} for '{item['filename']}'. Retrying in {wait_time:.0f}s...", "warning")
                                    time.sleep(wait_time)
                                else:
                                    # Final failure: set a status for re-selection/resumption
                                    item['upload_status_detail'] = "Final Failure (Retry needed)"
                                    # Keep selected for the next session
                                    item['selected_for_upload'] = True 
                                    add_to_log(f"{progress_prefix} ❌ Final upload failed for '{item['filename']}': {e}", "error")
                                    
                    add_to_log(f"🏁 Batch upload process complete. Processed {total_to_upload} selected files.", "info")
                    with log_placeholder.expander("Live Activity Log", expanded=True):
                        for log in st.session_state.activity_log:
                            if log['status'] == 'success': st.success(log['message'])
                            elif log['status'] == 'error': st.error(log['message'])
                            elif log['status'] == 'warning': st.warning(log['message'])
                            else: st.info(log['message'])
                    st.success("Google Drive upload process finished. See log for details.")
                else: st.error("Google Drive authentication failed.")
            # --- END UPLOAD RESILIENCE CHANGES ---
        else: st.info("Google Drive features are disabled.")

    with tab_download:
        if st.session_state.user_prefs.get("enable_local_download", True):
            
            # MODIFIED: Reads from temp_file_path instead of file_bytes
            def create_zip(items):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for item in items:
                        path = item.get('temp_file_path')
                        if 'filename' in item and path and os.path.exists(path):
                            try:
                                with open(path, "rb") as f:
                                    zf.writestr(item['filename'], f.read())
                            except Exception as e:
                                add_to_log(f"Warning: Could not read temp file {path} for ZIP. {e}", "warning")
                return buf.getvalue()
            
            matched = [item for item in st.session_state.processed_payslips_data if item['status'] == "Details Extracted"]
            if matched: st.download_button("⬇️ Download Matched (ZIP)", data=create_zip(matched), file_name="Matched_Payslips.zip", mime="application/zip")
            if st.session_state.processed_payslips_data: st.download_button("⬇️ Download All Processed (ZIP)", data=create_zip(st.session_state.processed_payslips_data), file_name="All_Processed_Payslips.zip", mime="application/zip")
        else: st.info("Local download is disabled.")

    # Admin Sidebar
    st.sidebar.markdown("### 🔐 Admin Login")
    admin_pw = st.sidebar.text_input("Enter admin password", type="password", key="admin_pw")
    is_admin = admin_pw and admin_pw == st.secrets.get("admin_password", "admin")

    if is_admin:
        st.sidebar.success("✅ Admin access granted")
        st.sidebar.markdown("---")
        st.sidebar.subheader("🛠 Upload Log Maintenance")
        if st.sidebar.button("🗑 Reset Upload Log"):
            try:
                with open(UPLOAD_LOG, "w") as f: json.dump([], f)
                st.session_state.uploaded_file_keys_log = set()
                st.sidebar.success("Upload log reset.")
            except Exception as e:
                st.sidebar.error(f"Failed to reset log: {e}")
        st.sidebar.info(f"📊 {len(st.session_state.uploaded_file_keys_log)} files logged as uploaded.")
        if st.sidebar.checkbox("📂 Show Upload Log"):
            st.sidebar.json(list(st.session_state.uploaded_file_keys_log))
    elif admin_pw:
        st.sidebar.error("Incorrect admin password.")
