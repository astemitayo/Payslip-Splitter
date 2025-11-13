# Code Hybrid - Unified & Modular (drop-in replacement)
import os
import io
import re
import json
import zipfile
import tempfile
import base64
import streamlit as st

from PyPDF2 import PdfReader, PdfWriter
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Optional OCR libs (used only when OCR is selected)
from pdf2image import convert_from_bytes
import pytesseract

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
    "ocr_mode": "Hybrid"  # options: Normal, Hybrid, Full OCR
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
# Google Drive helpers
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

def upload_file_to_google_drive(service, filename, file_bytes, mime_type="application/pdf"):
    try:
        file_metadata = {"name": filename, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
        return file.get("id")
    except Exception as e:
        raise

# -----------------------------
# Text/detail extraction utilities
# -----------------------------
def get_details_from_text(merged_text, group_idx=None):
    """
    Extract Year, Month, and IPPIS Number from payslip text.
    IPPIS MUST be exactly six digits (numeric only).
    Returns dict or None.
    """
    if not merged_text:
        return None

    # normalize
    text = merged_text
    text_upper = text.upper()

    # month mapping (full names allowed)
    month_map = {
        'JANUARY': '01','FEBRUARY': '02','MARCH': '03','APRIL': '04',
        'MAY': '05','JUNE': '06','JULY': '07','AUGUST': '08',
        'SEPTEMBER': '09','OCTOBER': '10','NOVEMBER': '11','DECEMBER': '12'
    }

    # year
    year_match = re.search(r'\b(20\d{2})\b', text)
    year = year_match.group(1) if year_match else None

    # month (try MON-YYYY or full month names)
    month_abbr_match = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\-\s]?\s*(20\d{2})\b', text, re.IGNORECASE)
    month = None
    if month_abbr_match:
        abbr = month_abbr_match.group(1).upper()
        abbr_map = {'JAN':'01','FEB':'02','MAR':'03','APR':'04','MAY':'05','JUN':'06','JUL':'07','AUG':'08','SEP':'09','OCT':'10','NOV':'11','DEC':'12'}
        month = abbr_map.get(abbr)
        if not year:
            year = month_abbr_match.group(2)

    if not month:
        full_month_match = re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\b', text, re.IGNORECASE)
        if full_month_match:
            month = month_map.get(full_month_match.group(1).capitalize())

    # IPPIS: enforce exactly six digits
    ippis_match = re.search(r'\b(\d{6})\b', text)  # exactly 6 digits
    ippis_number = ippis_match.group(1) if ippis_match else None

    if year and month and ippis_number:
        return {'year': year, 'month': month, 'ippis_number': ippis_number}
    return None

# -----------------------------
# Core text extraction helpers
# -----------------------------
def extract_text_from_pdf_non_ocr(reader):
    """
    Extract text using PyPDF2 for each page. Returns list of page texts.
    """
    texts = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        texts.append(text)
    return texts

def extract_text_page_ocr(pdf_bytes, page_index, dpi=150):
    """
    Perform OCR on a single page (page_index is 0-based).
    Returns string text for that page.
    """
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=page_index+1, last_page=page_index+1)
        if not pages:
            return ""
        text = pytesseract.image_to_string(pages[0])
        return text or ""
    except Exception as e:
        # Return empty string and let caller decide what to do
        st.warning(f"OCR page {page_index+1} failed: {e}")
        return ""

def extract_all_pages_ocr(pdf_bytes, num_pages_total, dpi=150):
    """
    OCR every page in the PDF. Returns list of texts (one per page).
    Processes page-by-page to limit memory.
    """
    ocr_texts = []
    ocr_progress_bar = st.progress(0, text=f"Performing Full OCR on {num_pages_total} pages...")
    for i in range(num_pages_total):
        txt = extract_text_page_ocr(pdf_bytes, i, dpi=dpi)
        ocr_texts.append(txt)
        ocr_progress_bar.progress((i+1)/num_pages_total, text=f"Full OCR: Page {i+1}/{num_pages_total}")
    ocr_progress_bar.empty()
    return ocr_texts

# -----------------------------
# Grouping function
# -----------------------------
def group_pages_by_payslip_from_texts(page_texts, pdf_num_pages):
    """
    Groups pages into payslips based on textual markers. Falls back to 1-page-per-payslip.
    """
    payslip_groups = []
    current_group = []

    for i, text in enumerate(page_texts):
        text_upper = (text or "").upper()

        # Start marker
        if "FEDERAL GOVERNMENT OF NIGERIA" in text_upper:
            if current_group:
                payslip_groups.append(current_group)
                current_group = []
        current_group.append(i)

        # End marker
        if "TOTAL NET EARNINGS" in text_upper and current_group and i in current_group:
            payslip_groups.append(current_group)
            current_group = []

    if current_group:
        payslip_groups.append(current_group)

    # Fallback: if grouping didn't work, treat each page as its own payslip
    if not payslip_groups or (len(payslip_groups) == 1 and len(payslip_groups[0]) == pdf_num_pages):
        st.info("No distinct payslip markers found. Falling back to treating each page as a separate payslip.")
        return [[i] for i in range(pdf_num_pages)]

    return payslip_groups

# -----------------------------
# Unified splitting function
# -----------------------------
# Note: Avoid caching heavy binary objects with st.cache_data; remove cache to ensure fresh processing
def split_and_rename_pdf_dynamic(input_pdf_bytes, ocr_mode="Hybrid", naming_pattern="{year} {month} {ippis}"):
    """
    input_pdf_bytes: bytes of uploaded PDF
    ocr_mode: "Normal", "Hybrid", or "Full OCR"
    Returns list of dicts: {key, filename, file_bytes, year, month, ippis, status, selected_for_upload}
    """
    processed = []
    try:
        reader = PdfReader(io.BytesIO(input_pdf_bytes))
        num_pages = len(reader.pages)

        # STEP 1: extract texts based on mode
        if ocr_mode == "Full OCR":
            page_texts = extract_all_pages_ocr(input_pdf_bytes, num_pages)
        else:
            # Extract non-OCR first
            st.toast(f"Extracting text from {num_pages} page(s) (mode={ocr_mode})...", icon="📄")
            progress_bar = st.progress(0, text="Extracting text (PyPDF2)...")
            non_ocr_texts = extract_text_from_pdf_non_ocr(reader)

            if ocr_mode == "Normal":
                page_texts = non_ocr_texts
                progress_bar.progress(1.0, text="PyPDF2 extraction complete.")
                progress_bar.empty()
            else:  # Hybrid: fallback to OCR on per-page basis
                page_texts = []
                for i, txt in enumerate(non_ocr_texts):
                    candidate = txt or ""
                    # Heuristic: if too short or missing known markers, fall back to OCR
                    if candidate and len(candidate.strip()) >= 80 and ("FEDERAL GOVERNMENT" in candidate.upper() or re.search(r'\b(20\d{2})\b', candidate)):
                        page_texts.append(candidate)
                    else:
                        # OCR fallback for this page
                        ocr_txt = extract_text_page_ocr(input_pdf_bytes, i)
                        if ocr_txt and len(ocr_txt.strip()) > len(candidate.strip()):
                            page_texts.append(ocr_txt)
                        else:
                            # keep whatever non-OCR gave (even if empty)
                            page_texts.append(candidate)
                    progress_bar.progress((i+1)/num_pages, text=f"Hybrid extraction (page {i+1}/{num_pages})")
                progress_bar.empty()

        # STEP 2: grouping
        page_groups = group_pages_by_payslip_from_texts(page_texts, num_pages)
        st.toast(f"Found {len(page_groups)} potential payslip document(s).", icon="✂️")

        # STEP 3: assemble grouped PDFs, extract details and prepare outputs
        payslip_progress = st.progress(0, text=f"Processing {len(page_groups)} payslips...")
        for g_idx, group in enumerate(page_groups, start=1):
            writer = PdfWriter()
            merged_text = ""
            for pg in group:
                writer.add_page(reader.pages[pg])
                merged_text += (page_texts[pg] or "") + "\n"

            details = get_details_from_text(merged_text, g_idx)
            info = {
                'key': None,
                'filename': None,
                'file_bytes': None,
                'year': None,
                'month': None,
                'ippis': None,
                'status': 'Details Missing',
                'selected_for_upload': False
            }

            if details:
                info['year'] = details['year']
                info['month'] = details['month']
                info['ippis'] = details['ippis_number']
                info['key'] = f"{details['year']}_{details['month']}_{details['ippis_number']}_{g_idx}"
                fname = naming_pattern.format(year=details["year"], month=details["month"], ippis=details["ippis_number"])
                if not fname.lower().endswith(".pdf"):
                    fname += ".pdf"
                info['filename'] = fname
                info['status'] = "Details Extracted"
                info['selected_for_upload'] = True
            else:
                info['key'] = f"no_details_group_{g_idx}_from_uploaded_pdf"
                info['filename'] = f"Payslip_Group_{g_idx}_missing_details.pdf"
                info['status'] = "Details Missing"
                info['selected_for_upload'] = False

            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)
            info['file_bytes'] = buf.read()
            processed.append(info)

            payslip_progress.progress(g_idx / len(page_groups), text=f"Processed payslip {g_idx}/{len(page_groups)}")

        payslip_progress.empty()
        st.success("✅ All pages processed successfully!")
        return processed

    except Exception as e:
        st.error(f"Error while processing PDF: {e}")
        return []

# -----------------------------
# App Instructions & Uploader
# -----------------------------
st.markdown("""
Upload a multi-page PDF containing payslips. The app can:
- split single-page payslips,
- group multi-page payslips (detected via markers),
- use OCR (full/hybrid) for scanned PDFs,
- allow review and selective upload to Google Drive and/or provide ZIP downloads.
""")

uploaded_file = st.file_uploader("📂 Upload a PDF containing payslips", type="pdf", help="Drag & drop or click to browse")

# Session initialization
if 'processed_payslips_data' not in st.session_state:
    st.session_state.processed_payslips_data = []
if 'uploaded_file_keys_log' not in st.session_state:
    st.session_state.uploaded_file_keys_log = set()
    UPLOAD_LOG = "uploaded_files.json"
    if os.path.exists(UPLOAD_LOG):
        try:
            with open(UPLOAD_LOG, "r") as f:
                st.session_state.uploaded_file_keys_log = set(json.load(f))
        except Exception:
            pass

if uploaded_file:
    st.success("File uploaded successfully!")

    if st.button("🚀 Split & Process Payslips", key="process_button"):
        st.session_state.processed_payslips_data = []
        # Clear any cached function if needed - here we call the function fresh each time

        ocr_mode = st.session_state.user_prefs.get("ocr_mode", "Hybrid")
        naming_pattern = st.session_state.user_prefs.get("naming_pattern", "{year} {month} {ippis}")

        processed_data = split_and_rename_pdf_dynamic(uploaded_file.getvalue(), ocr_mode=ocr_mode, naming_pattern=naming_pattern)
        st.session_state.processed_payslips_data = processed_data

        # initialize upload selection flags & status
        for item in st.session_state.processed_payslips_data:
            if item['key'] in st.session_state.uploaded_file_keys_log:
                item['selected_for_upload'] = False
                item['upload_status_detail'] = 'Already uploaded'
            else:
                item['selected_for_upload'] = (item['status'] == "Details Extracted")
                item['upload_status_detail'] = 'Pending'

# If there are processed payslips, present review UI (same style as your Hybrid code)
if st.session_state.processed_payslips_data:
    st.markdown("---")
    st.subheader("📊 Review & Select Payslips")

    col_sel_all, col_desel_all = st.columns(2)
    if col_sel_all.button("✅ Select All for Upload", key="select_all"):
        for item in st.session_state.processed_payslips_data:
            if item.get('upload_status_detail', '') != 'Already uploaded':
                item['selected_for_upload'] = True
    if col_desel_all.button("❌ Deselect All for Upload", key="deselect_all"):
        for item in st.session_state.processed_payslips_data:
            item['selected_for_upload'] = False

    display_data = []
    for item in st.session_state.processed_payslips_data:
        display_data.append({
            "Selected": item['selected_for_upload'],
            "Filename": item['filename'],
            "Year": item['year'] if item['year'] else "-",
            "Month": item['month'] if item['month'] else "-",
            "IPPIS": item['ippis'] if item['ippis'] else "-",
            "Processing Status": item['status'],
            "Upload Status": item.get('upload_status_detail', 'N/A')
        })

    edited_data = st.data_editor(
        display_data,
        column_config={
            "Selected": st.column_config.CheckboxColumn("Upload?", help="Select to upload this payslip to Google Drive", default=False),
            "Filename": st.column_config.TextColumn("Filename", width="large"),
            "Year": "Year",
            "Month": "Month",
            "IPPIS": "IPPIS No.",
            "Processing Status": "Processing Status",
            "Upload Status": "Upload Status",
        },
        hide_index=True,
        key="payslip_selection_editor",
    )

    # Sync selections back to session_state
    for i, row in enumerate(edited_data):
        if i < len(st.session_state.processed_payslips_data):
            st.session_state.processed_payslips_data[i]['selected_for_upload'] = row['Selected']

    tab_drive, tab_download = st.tabs(["☁️ Google Drive Actions", "💻 Local Download"])

    with tab_drive:
        if st.session_state.user_prefs.get("enable_drive_upload", True):
            selected_for_upload = [item for item in st.session_state.processed_payslips_data if item['selected_for_upload']]
            st.info(f"You have **{len(selected_for_upload)}** payslips selected for Google Drive upload.")

            if st.button("⬆️ Upload Selected to Google Drive", key="upload_selected_button", disabled=not selected_for_upload):
                service = authenticate_google_drive()
                if service:
                    progress_text = "Uploading payslips to Google Drive. Please wait."
                    upload_progress_bar = st.progress(0, text=progress_text)
                    total_to_upload = len(selected_for_upload)
                    uploaded_count = 0

                    for item in selected_for_upload:
                        key = item['key']
                        filename = item['filename']
                        file_bytes = item['file_bytes']

                        if key in st.session_state.uploaded_file_keys_log:
                            item['upload_status_detail'] = "Skipped (Already Logged)"
                            uploaded_count += 1
                            upload_progress_bar.progress(uploaded_count/total_to_upload, text=f"{progress_text} ({filename}: Skipped)")
                            continue

                        try:
                            st.toast(f"Uploading {filename}...", icon="🚀")
                            file_id = upload_file_to_google_drive(service, filename, file_bytes)
                            item['upload_status_detail'] = f"Uploaded (ID: {file_id})"
                            st.session_state.uploaded_file_keys_log.add(key)
                            st.toast(f"Uploaded {filename} successfully!", icon="✅")
                        except Exception as e:
                            item['upload_status_detail'] = f"Failed ({e})"
                            st.error(f"Failed to upload {filename}: {e}")

                        uploaded_count += 1
                        upload_progress_bar.progress(uploaded_count/total_to_upload, text=f"{progress_text} ({filename}: {item['upload_status_detail']})")

                    upload_progress_bar.empty()
                    st.success(f"Google Drive upload process complete. {uploaded_count} files attempted.")

                    # Persist updated upload log
                    try:
                        UPLOAD_LOG = "uploaded_files.json"
                        with open(UPLOAD_LOG, "w") as f:
                            json.dump(list(st.session_state.uploaded_file_keys_log), f)
                    except Exception:
                        st.warning("Could not save updated upload log to disk.")
                else:
                    st.warning("Google Drive authentication failed. Cannot upload.")
        else:
            st.info("Google Drive features are disabled in settings.")

    with tab_download:
        if st.session_state.user_prefs.get("enable_local_download", True):
            matched_pdfs_with_keys = [
                (item['key'], item['filename'], item['file_bytes'])
                for item in st.session_state.processed_payslips_data
                if item['status'] == "Details Extracted"
            ]
            all_pdfs_with_keys = [
                (item['key'], item['filename'], item['file_bytes'])
                for item in st.session_state.processed_payslips_data
            ]

            if matched_pdfs_with_keys:
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for key, filename, file_bytes in matched_pdfs_with_keys:
                        zf.writestr(filename, file_bytes)
                zip_buffer.seek(0)
                st.download_button("⬇️ Download Matched Payslips (ZIP)", data=zip_buffer, file_name="Matched_Payslips.zip", mime="application/zip", key="download_matched_zip")
            else:
                st.info("No payslips with extractable details were found to download locally.")

            if all_pdfs_with_keys:
                zip_buffer_all = io.BytesIO()
                with zipfile.ZipFile(zip_buffer_all, "w", zipfile.ZIP_DEFLATED) as zf:
                    for key, filename, file_bytes in all_pdfs_with_keys:
                        zf.writestr(filename, file_bytes)
                zip_buffer_all.seek(0)
                st.download_button("⬇️ Download All Processed Payslips (ZIP)", data=zip_buffer_all, file_name="All_Processed_Payslips.zip", mime="application/zip", key="download_all_zip")
            elif not matched_pdfs_with_keys:
                st.info("No pages were processed for local download.")
        else:
            st.info("Local download is disabled in settings.")

    # Admin sidebar (same as before)
    st.sidebar.markdown("### 🔐 Admin Login")
    admin_pw = st.sidebar.text_input("Enter admin password", type="password")
    is_admin = admin_pw == st.secrets.get("admin_password", "")

    if is_admin:
        st.sidebar.success("✅ Admin access granted")
        st.sidebar.markdown("---")
        st.sidebar.subheader("🛠 Upload Log Maintenance")

        if st.sidebar.button("🗑 Reset Upload Log"):
            try:
                UPLOAD_LOG = "uploaded_files.json"
                with open(UPLOAD_LOG, "w") as f:
                    json.dump([], f)
                st.session_state.uploaded_file_keys_log = set()
                st.sidebar.success("Upload log has been reset.")
            except Exception as e:
                st.sidebar.error(f"Failed to reset log: {e}")

        st.sidebar.info(f"📊 {len(st.session_state.uploaded_file_keys_log)} files currently logged as uploaded.")
        if st.sidebar.checkbox("📂 Show Upload Log", key="show_upload_log_admin"):
            st.sidebar.write(list(st.session_state.uploaded_file_keys_log))
    else:
        st.sidebar.info("👤 Standard user mode (Admin tools hidden)")
