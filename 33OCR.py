# merged_payslip_app.py
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

# Set sensible defaults if keys missing
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
    "Upload to Google Drive",
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

st.session_state.user_prefs["timezone"] = st.sidebar.selectbox(
    "Timezone", ["Africa/Lagos", "UTC", "Europe/London"],
    index=["Africa/Lagos", "UTC", "Europe/London"].index(st.session_state.user_prefs["timezone"]),
    key="timezone"
)

st.session_state.user_prefs["date_format"] = st.sidebar.radio(
    "Date format", ["YYYY-MM-DD", "DD/MM/YYYY", "MM-YYYY"],
    index=["YYYY-MM-DD", "DD/MM/YYYY", "MM-YYYY"].index(st.session_state.user_prefs["date_format"]),
    key="date_format"
)

st.session_state.user_prefs["ocr_mode"] = st.sidebar.selectbox(
    "OCR Mode",
    options=["Normal", "Hybrid", "Full OCR"],
    index=["Normal", "Hybrid", "Full OCR"].index(st.session_state.user_prefs.get("ocr_mode", "Hybrid")),
    help="Normal = PyPDF2 text only. Hybrid = text first, OCR fallback per-page. Full OCR = OCR every page.",
    key="ocr_mode"
)

# Persist preferences immediately
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
# Text/detail extraction utils
# -----------------------------
def get_details_from_text(text):
    """Extract Year, Month, and IPPIS Number from payslip text. Returns dict or None."""
    try:
        year_match = re.search(r'\b(20\d{2})\b', text)
        year = year_match.group(1) if year_match else None

        month_abbr_match = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{4}\b', text, re.IGNORECASE)
        month = None
        if month_abbr_match:
            month_map = {
                'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
            }
            month = month_map.get(month_abbr_match.group(1).upper())

        if not month:
            full_month_match = re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b', text, re.IGNORECASE)
            if full_month_match:
                month_map_full = {
                    'January': '01', 'February': '02', 'March': '03', 'April': '04', 'May': '05', 'June': '06',
                    'July': '07', 'August': '08', 'September': '09', 'October': '10', 'November': '11', 'December': '12'
                }
                month = month_map_full.get(full_month_match.group(1).capitalize())
                if not year:
                    year = full_month_match.group(2)

        ippis_match = re.search(r'IPPIS\s*Number[:\-]?\s*(\w+)', text, re.IGNORECASE)
        ippis_number = ippis_match.group(1) if ippis_match else None

        if not ippis_number:
            ippis_number_generic_match = re.search(r'\b(\d{6,10})\b', text)
            if ippis_number_generic_match:
                ippis_number = ippis_number_generic_match.group(1)

        if year and month and ippis_number:
            return {'year': year, 'month': month, 'ippis_number': ippis_number}
        return None
    except Exception:
        return None

# -----------------------------
# OCR / Non-OCR extraction functions
# -----------------------------
def extract_text_from_pdf_non_ocr(reader):
    texts = []
    for page in reader.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            texts.append("")
    return texts

def extract_text_page_ocr(pdf_bytes, page_index):
    # pdf2image uses 1-based page indices
    images = convert_from_bytes(pdf_bytes, dpi=150, first_page=page_index + 1, last_page=page_index + 1)
    if not images:
        return ""
    return pytesseract.image_to_string(images[0])

def extract_all_pages_ocr(pdf_bytes):
    images = convert_from_bytes(pdf_bytes, dpi=150)
    texts = []
    for img in images:
        texts.append(pytesseract.image_to_string(img))
    return texts

def group_pages_by_payslip_from_texts(texts):
    groups = []
    current = []
    for i, t in enumerate(texts):
        tu = (t or "").upper()
        # Start marker heuristic
        if ("FEDERAL GOVERNMENT OF NIGERIA" in tu or "PAYSLIP" in tu) and current:
            groups.append(current)
            current = []
        current.append(i)
        # End marker heuristics
        if any(k in tu for k in ("TOTAL NET EARNINGS", "NET PAY", "NET SALARY", "NET EARNINGS")):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    # If grouping produced only trivial singletons and no markers found, return empty to signal fallback to per-page grouping
    found_markers = any(any(m in (t or "").upper() for m in ("FEDERAL GOVERNMENT OF NIGERIA", "TOTAL NET EARNINGS", "NET PAY", "PAYSLIP")) for t in texts)
    if not found_markers:
        return []
    return groups

# -----------------------------
# Main splitting function (hybrid support)
# -----------------------------
def split_and_rename_pdf_with_modes(input_pdf_path, ocr_mode="Hybrid", naming_pattern="{year} {month} {ippis}"):
    """
    input_pdf_path: path to saved pdf file
    ocr_mode: "Normal", "Hybrid", or "Full OCR"
    Returns: all_processed_files_with_keys, matched_files_with_keys
    """
    all_processed_files_with_keys = []
    matched_files_with_keys = []
    try:
        with open(input_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        reader = PdfReader(io.BytesIO(pdf_bytes))
        num_pages = len(reader.pages)
        progress = st.progress(0)

        # Build page_texts according to mode
        page_texts = []
        st.info(f"Processing {num_pages} page(s) in {ocr_mode} mode...")

        if ocr_mode == "Full OCR":
            page_texts = extract_all_pages_ocr(pdf_bytes)

        else:
            # Start with non-OCR extraction
            non_ocr_texts = extract_text_from_pdf_non_ocr(reader)
            if ocr_mode == "Normal":
                page_texts = non_ocr_texts
            else:  # Hybrid
                # For each page, use non-ocr text unless it's too short -> fallback to OCR
                for i, txt in enumerate(non_ocr_texts):
                    if txt and len(txt.strip()) >= 60:
                        page_texts.append(txt)
                    else:
                        # fallback OCR for this page
                        try:
                            ocr_txt = extract_text_page_ocr(pdf_bytes, i)
                            page_texts.append(ocr_txt)
                        except Exception:
                            page_texts.append(txt or "")

        # Attempt grouping using the texts (works for OCR or good non-OCR extraction)
        page_groups = group_pages_by_payslip_from_texts(page_texts)
        if not page_groups:
            # fallback: each page is its own group
            page_groups = [[i] for i in range(num_pages)]

        # Now iterate groups and write files
        for g_index, group in enumerate(page_groups, start=1):
            writer = PdfWriter()
            merged_text = ""
            for pg in group:
                writer.add_page(reader.pages[pg])
                merged_text += (page_texts[pg] or "") + "\n"

            details = get_details_from_text(merged_text)
            if details:
                identity_key = f"{details['year']}_{details['month']}_{details['ippis_number']}"
                filename = naming_pattern.format(year=details["year"], month=details["month"], ippis=details["ippis_number"])
                if not filename.lower().endswith(".pdf"):
                    filename += ".pdf"
            else:
                identity_key = f"pagegroup_{g_index}_no_details_{os.path.basename(input_pdf_path)}"
                filename = f"Payslip_Group_{g_index}_missing_details.pdf"

            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)
            file_bytes = buf.read()

            all_processed_files_with_keys.append((identity_key, filename, file_bytes))
            if details:
                matched_files_with_keys.append((identity_key, filename, file_bytes))

            progress.progress(min(g_index / max(1, len(page_groups)), 1.0))

        progress.empty()
        st.success("✅ All pages processed successfully!")
        return all_processed_files_with_keys, matched_files_with_keys

    except Exception as e:
        st.error(f"Error while processing PDF: {e}")
        return [], []

# -----------------------------
# App Instructions & Uploader
# -----------------------------
st.markdown("""
Upload a multi-page PDF containing payslips. The app can:
- split single-page payslips,
- group multi-page payslips (detected via markers),
- use OCR (full/hybrid) for scanned PDFs,
- upload matched payslips to Google Drive and/or provide ZIP downloads.
""")

uploaded_file = st.file_uploader("📂 Upload a PDF containing payslips", type="pdf", help="Drag & drop or click to browse")

if uploaded_file:
    st.success("File uploaded successfully!")

    if st.button("🚀 Split & Process Payslips"):
        # Save to a temp file path on disk (so pdf2image can work reliably)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_path = tmp_file.name

        try:
            ocr_mode = st.session_state.user_prefs.get("ocr_mode", "Hybrid")
            naming_pattern = st.session_state.user_prefs.get("naming_pattern", "{year} {month} {ippis}")

            all_pdfs_with_keys, matched_pdfs_with_keys = split_and_rename_pdf_with_modes(
                tmp_path, ocr_mode=ocr_mode, naming_pattern=naming_pattern
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        if all_pdfs_with_keys:
            tab1, tab2 = st.tabs(["☁️ Google Drive Upload", "💻 Local Download"])

            # -------- Google Drive Upload Tab --------
            with tab1:
                if st.session_state.user_prefs.get("enable_drive_upload", True):
                    service = authenticate_google_drive()
                    if service and matched_pdfs_with_keys:
                        st.info(f"Found {len(matched_pdfs_with_keys)} potential payslips to upload.")

                        UPLOAD_LOG = "uploaded_files.json"
                        if os.path.exists(UPLOAD_LOG):
                            try:
                                with open(UPLOAD_LOG, "r") as f:
                                    uploaded_file_keys = set(json.load(f))
                            except Exception:
                                uploaded_file_keys = set()
                        else:
                            uploaded_file_keys = set()

                        # --- Initialize status table ---
                        status_data = []
                        for key, filename, file_bytes in matched_pdfs_with_keys:
                            if key in uploaded_file_keys:
                                status_data.append({"filename": filename, "status": "⏩ Skipped (Already Uploaded)"})
                            else:
                                status_data.append({"filename": filename, "status": "⏳ Pending Upload"})

                        status_placeholder = st.empty()
                        status_placeholder.table(status_data)
                        progress_bar = st.progress(0)

                        total = len(matched_pdfs_with_keys)
                        completed = 0
                        new_uploads = 0

                        for key, filename, file_bytes in matched_pdfs_with_keys:
                            if key in uploaded_file_keys:
                                completed += 1
                                progress_bar.progress(completed / total)
                                continue

                            # Update status to uploading in the table
                            for row in status_data:
                                if row["filename"] == filename:
                                    row["status"] = "🔄 Uploading..."
                                    break
                            status_placeholder.table(status_data)

                            try:
                                file_id = upload_file_to_google_drive(service, filename, file_bytes)
                                for row in status_data:
                                    if row["filename"] == filename:
                                        row["status"] = f"✅ Uploaded (ID: {file_id})"
                                        break
                                uploaded_file_keys.add(key)
                                new_uploads += 1
                            except Exception as e:
                                for row in status_data:
                                    if row["filename"] == filename:
                                        row["status"] = f"❌ Failed ({e})"
                                        break

                            completed += 1
                            progress_bar.progress(completed / total)
                            status_placeholder.table(status_data)

                        # Save updated log
                        try:
                            with open(UPLOAD_LOG, "w") as f:
                                json.dump(list(uploaded_file_keys), f)
                        except Exception:
                            st.warning("Could not save upload log to disk.")

                        st.info(f"Upload complete. {new_uploads} new files uploaded, {total - new_uploads} skipped.")
                    elif not service:
                        st.warning("Google Drive upload is enabled but authentication failed. Skipping upload.")
                    else:
                        st.info("No valid payslips found with extractable details for upload, or no files to upload.")
                else:
                    st.info("Google Drive upload is disabled in settings.")

            # -------- Local Download Tab --------
            with tab2:
                if st.session_state.user_prefs.get("enable_local_download", True):
                    if matched_pdfs_with_keys:
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                            for key, filename, file_bytes in matched_pdfs_with_keys:
                                zf.writestr(filename, file_bytes)
                        zip_buffer.seek(0)
                        st.download_button(
                            "⬇️ Download Matched Payslips (ZIP)",
                            data=zip_buffer,
                            file_name="Matched_Payslips.zip",
                            mime="application/zip"
                        )
                    else:
                        st.info("No payslips with extractable details were found to download locally.")

                    if all_pdfs_with_keys:
                        zip_buffer_all = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer_all, "w", zipfile.ZIP_DEFLATED) as zf:
                            for key, filename, file_bytes in all_pdfs_with_keys:
                                zf.writestr(filename, file_bytes)
                        zip_buffer_all.seek(0)
                        st.download_button(
                            "⬇️ Download All Processed Payslips (ZIP)",
                            data=zip_buffer_all,
                            file_name="All_Processed_Payslips.zip",
                            mime="application/zip"
                        )
                    elif not matched_pdfs_with_keys:
                        st.info("No pages were processed for local download.")
                else:
                    st.info("Local download is disabled in settings.")

            # -------- Admin Sidebar (same as before) --------
            st.sidebar.markdown("### 🔐 Admin Login")
            admin_pw = st.sidebar.text_input("Enter admin password", type="password")
            is_admin = admin_pw == st.secrets.get("admin_password", "")

            if is_admin:
                st.sidebar.success("✅ Admin access granted")
                st.sidebar.markdown("---")
                st.sidebar.subheader("🛠 Upload Log Maintenance")

                if st.sidebar.button("🗑 Reset Upload Log"):
                    try:
                        with open("uploaded_files.json", "w") as f:
                            json.dump([], f)
                        st.sidebar.success("Upload log has been reset.")
                    except Exception as e:
                        st.sidebar.error(f"Failed to reset log: {e}")

                if os.path.exists("uploaded_files.json"):
                    try:
                        with open("uploaded_files.json", "r") as f:
                            uploaded_debug = json.load(f)
                        st.sidebar.info(f"📊 {len(uploaded_debug)} files currently logged as uploaded.")
                        if st.sidebar.checkbox("📂 Show Upload Log", key="show_upload_log"):
                            if all(isinstance(entry, str) for entry in uploaded_debug):
                                st.sidebar.write(uploaded_debug)
                            elif all(isinstance(entry, dict) for entry in uploaded_debug):
                                st.sidebar.table(uploaded_debug)
                            else:
                                st.sidebar.json(uploaded_debug)
                    except Exception as e:
                        st.sidebar.error(f"Failed to read upload log: {e}")
            else:
                st.sidebar.info("👤 Standard user mode (Admin tools hidden)")

        else:
            st.info("No pages were processed. Check your PDF and try again.")

