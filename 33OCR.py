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
from pdf2image import convert_from_bytes # Changed from convert_from_path
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
    "enable_drive_upload": True, # Keep this as a general toggle for showing the option
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

# We'll keep this setting as a master toggle for showing the GDrive section
st.session_state.user_prefs["enable_drive_upload"] = st.sidebar.checkbox(
    "Enable Google Drive features", # Renamed for clarity
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
        # Accessing st.secrets["gcp_service_account"] which is a dictionary due to TOML parsing
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
# Text/detail extraction utils - ADAPTED FROM YOUR WORKING SCRIPT
# -----------------------------
def get_details_from_text(merged_text, group_idx):
    """
    Extract Year, Month, and IPPIS Number from payslip text using the robust regexes.
    Returns dict or None.
    """
    text_upper = merged_text.upper()
    
    month_map = {
        'JANUARY': '01', 'FEBRUARY': '02', 'MARCH': '03', 'APRIL': '04',
        'MAY': '06', 'JUNE': '06', 'JULY': '07', 'AUGUST': '08', # Corrected MAY to 05, was 06
        'SEPTEMBER': '09', 'OCTOBER': '10', 'NOVEMBER': '11', 'DECEMBER': '12'
    }

    # --- Extract metadata ---
    year_match = re.search(r'\b(20\d{2})\b', merged_text)
    month_match = re.search(
        r'\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\b',
        merged_text, re.IGNORECASE
    )

    # --- Robust IPPIS detection (order matters for prioritization) ---
    ippis = None
    m1 = re.search(r'IPPIS\s*Number[:\-]?\s*(\d{3,10})', merged_text, re.IGNORECASE) # Added max digits for IPPIS
    m2 = re.search(r'(\d{3,10})\s*Step', merged_text, re.IGNORECASE)
    m3 = re.search(r'FGN\s+CIVIL\s+SERVICE.*?(\d{5,10})', merged_text, re.IGNORECASE | re.DOTALL)
    m4 = re.search(r'FEDERAL\s+GOVERNMENT.*?(\d{5,10})', merged_text, re.IGNORECASE | re.DOTALL)
    m5 = re.search(r'\b(\d{6,10})\b', merged_text) # General 6-10 digit number as last resort

    if m1: ippis = m1.group(1)
    elif m2: ippis = m2.group(1)
    elif m3: ippis = m3.group(1)
    elif m4: ippis = m4.group(1)
    elif m5: ippis = m5.group(1)

    year = year_match.group(1) if year_match else None
    month = month_map.get(month_match.group(1).upper(), None) if month_match else None
    
    # Use 'Unknown' placeholders if not found
    if year and month and ippis:
        return {'year': year, 'month': month, 'ippis_number': ippis}
    return None

# -----------------------------
# OCR / Non-OCR extraction functions - Mostly kept the same for modularity
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
    images = convert_from_bytes(pdf_bytes, dpi=150, first_page=page_index + 1, last_page=page_index + 1)
    if not images:
        return ""
    return pytesseract.image_to_string(images[0])

def extract_all_pages_ocr(pdf_bytes):
    # This function is now crucial for the "Full OCR" mode.
    # It converts all pages to images and then OCRs them one by one,
    # similar to your local script's Step 1.
    reader = PdfReader(io.BytesIO(pdf_bytes))
    num_pages = len(reader.pages)
    
    ocr_texts = []
    for i in range(num_pages):
        # Convert only one page at a time to control memory use on Streamlit Cloud
        # No poppler_path needed if poppler-utils is in PATH
        pages = convert_from_bytes(pdf_bytes, dpi=150, first_page=i + 1, last_page=i + 1)
        if pages: # Ensure page was converted
            text = pytesseract.image_to_string(pages[0])
            ocr_texts.append(text)
        else:
            ocr_texts.append("") # Append empty if conversion fails for a page
    return ocr_texts


# -----------------------------
# Grouping function - ADAPTED FROM YOUR WORKING SCRIPT
# -----------------------------
def group_pages_by_payslip_from_texts(ocr_texts, pdf_num_pages):
    """
    Groups pages into individual payslips based on start/end markers
    as done in your local script.
    """
    payslip_groups = []
    current_group = []

    for i, text in enumerate(ocr_texts):
        text_upper = text.upper()

        # Start of new payslip
        if "FEDERAL GOVERNMENT OF NIGERIA" in text_upper:
            if current_group: # If we have pages in current_group, this means a new payslip started
                payslip_groups.append(current_group)
                current_group = [] # Start a new group
        current_group.append(i) # Add current page to the group

        # End of payslip
        # Only check for END marker if current_group is not empty to avoid creating empty groups
        if "TOTAL NET EARNINGS" in text_upper and current_group and i in current_group:
            # If an end marker is found and this page is part of the current group,
            # this means the current payslip is complete.
            # It's already been added to current_group, so just append and reset.
            payslip_groups.append(current_group)
            current_group = []

    # Add any remaining pages as a final group
    if current_group:
        payslip_groups.append(current_group)

    # Fallback if no groups were properly formed by the markers, or only one large group
    if not payslip_groups or (len(payslip_groups) == 1 and len(payslip_groups[0]) == pdf_num_pages):
        st.info("No distinct payslip markers found for intelligent grouping. Falling back to treating each page as a potential payslip.")
        return [[i] for i in range(pdf_num_pages)]

    return payslip_groups


# -----------------------------
# Main splitting function (hybrid support)
# -----------------------------
@st.cache_data(show_spinner="Processing PDF pages...")
def split_and_rename_pdf_with_modes(input_pdf_bytes, ocr_mode="Hybrid", naming_pattern="{year} {month} {ippis}"):
    """
    input_pdf_bytes: bytes of the pdf file
    ocr_mode: "Normal", "Hybrid", or "Full OCR"
    Returns: list of dictionaries, each representing a processed payslip part.
             Each dict has: {'key', 'filename', 'file_bytes', 'year', 'month', 'ippis', 'status'}
    """
    processed_payslips_data = []
    try:
        reader = PdfReader(io.BytesIO(input_pdf_bytes))
        num_pages = len(reader.pages)
        
        # Build page_texts according to mode
        page_texts = []
        st.toast(f"Extracting text from {num_pages} page(s) in {ocr_mode} mode...", icon="📄")

        if ocr_mode == "Full OCR":
            # Direct full OCR as in your working script
            page_texts = extract_all_pages_ocr(input_pdf_bytes)
        else:
            # Use Hybrid or Normal logic as before
            non_ocr_texts = extract_text_from_pdf_non_ocr(reader)
            if ocr_mode == "Normal":
                page_texts = non_ocr_texts
            else:  # Hybrid
                for i, txt in enumerate(non_ocr_texts):
                    if txt and len(txt.strip()) >= 60: # Threshold for considering non-OCR text "useful"
                        page_texts.append(txt)
                    else:
                        try:
                            ocr_txt = extract_text_page_ocr(input_pdf_bytes, i)
                            page_texts.append(ocr_txt)
                        except Exception as e:
                            st.warning(f"OCR fallback failed for page {i+1}: {e}")
                            page_texts.append(txt or "") # Use non-OCR if OCR fails

        # Group pages based on the extracted texts
        # Note: group_pages_by_payslip_from_texts expects ocr_texts now, which is `page_texts` here
        page_groups = group_pages_by_payslip_from_texts(page_texts, num_pages)

        st.toast(f"Found {len(page_groups)} potential payslip documents after grouping.", icon="✂️")

        for g_index, group in enumerate(page_groups, start=1):
            writer = PdfWriter()
            merged_text = ""
            for pg_idx in group: # pg_idx is the 0-based page number
                writer.add_page(reader.pages[pg_idx])
                merged_text += (page_texts[pg_idx] or "") + "\n"

            details = get_details_from_text(merged_text, g_index) # Pass g_index for potential fallback filename

            payslip_info = {
                'key': None, # Unique identifier for tracking
                'filename': None,
                'file_bytes': None,
                'year': None,
                'month': None,
                'ippis': None,
                'status': 'Details not found', # Initial status
                'selected_for_upload': False # New field for selection
            }

            if details:
                payslip_info['year'] = details["year"]
                payslip_info['month'] = details["month"]
                payslip_info['ippis'] = details["ippis_number"]
                # Use a more robust key to ensure uniqueness: year_month_ippis_groupindex
                payslip_info['key'] = f"{details['year']}_{details['month']}_{details['ippis_number']}_{g_index}"
                payslip_info['filename'] = naming_pattern.format(year=details["year"], month=details["month"], ippis=details["ippis_number"])
                if not payslip_info['filename'].lower().endswith(".pdf"):
                    payslip_info['filename'] += ".pdf"
                payslip_info['status'] = "Details Extracted"
            else:
                payslip_info['key'] = f"no_details_group_{g_index}_from_uploaded_pdf" # More generic if details missing
                payslip_info['filename'] = f"Payslip_Group_{g_index}_missing_details.pdf"
                payslip_info['status'] = "Details Missing" # Updated status

            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)
            payslip_info['file_bytes'] = buf.read()
            processed_payslips_data.append(payslip_info)
            
        st.success("✅ All pages processed successfully!")
        return processed_payslips_data

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

# Initialize session state for processed files
if 'processed_payslips_data' not in st.session_state:
    st.session_state.processed_payslips_data = []
if 'uploaded_file_keys_log' not in st.session_state:
    st.session_state.uploaded_file_keys_log = set()
    # Load existing log if available
    UPLOAD_LOG = "uploaded_files.json"
    if os.path.exists(UPLOAD_LOG):
        try:
            with open(UPLOAD_LOG, "r") as f:
                st.session_state.uploaded_file_keys_log = set(json.load(f))
        except Exception:
            pass # Ignore if log is corrupted or empty

if uploaded_file:
    st.success("File uploaded successfully!")

    if st.button("🚀 Split & Process Payslips", key="process_button"):
        # Clear previous processing results
        st.session_state.processed_payslips_data = []
        # Clear cache for the split function to ensure fresh processing
        split_and_rename_pdf_with_modes.clear()

        ocr_mode = st.session_state.user_prefs.get("ocr_mode", "Hybrid")
        naming_pattern = st.session_state.user_prefs.get("naming_pattern", "{year} {month} {ippis}")

        processed_data = split_and_rename_pdf_with_modes(
            uploaded_file.getvalue(), ocr_mode=ocr_mode, naming_pattern=naming_pattern
        )
        st.session_state.processed_payslips_data = processed_data
        # Initialize selection state if it's new data
        for i, item in enumerate(st.session_state.processed_payslips_data):
            if item['key'] in st.session_state.uploaded_file_keys_log:
                # If already uploaded, deselect by default for new upload round, but indicate it
                item['selected_for_upload'] = False
                item['upload_status_detail'] = 'Already uploaded'
            else:
                # Select by default if details extracted, otherwise deselect
                item['selected_for_upload'] = (item['status'] == "Details Extracted")
                item['upload_status_detail'] = 'Pending'


if st.session_state.processed_payslips_data:
    st.markdown("---")
    st.subheader("📊 Review & Select Payslips")

    # Select All / Deselect All functionality
    col_sel_all, col_desel_all = st.columns(2)
    if col_sel_all.button("✅ Select All for Upload", key="select_all"):
        for item in st.session_state.processed_payslips_data:
            # Only select if it's not already uploaded and currently 'pending'
            if item.get('upload_status_detail', '') != 'Already uploaded':
                item['selected_for_upload'] = True
    if col_desel_all.button("❌ Deselect All for Upload", key="deselect_all"):
        for item in st.session_state.processed_payslips_data:
            item['selected_for_upload'] = False

    # Display results in an editable table
    st.markdown("Use the checkboxes to select payslips for Google Drive upload.")

    # Prepare data for display
    display_data = []
    for i, item in enumerate(st.session_state.processed_payslips_data):
        display_data.append({
            "Selected": item['selected_for_upload'],
            "Filename": item['filename'],
            "Year": item['year'] if item['year'] else "-",
            "Month": item['month'] if item['month'] else "-",
            "IPPIS": item['ippis'] if item['ippis'] else "-",
            "Processing Status": item['status'],
            "Upload Status": item.get('upload_status_detail', 'N/A') # Show specific upload status
        })

    # Use st.data_editor for interactive selection
    # `on_change` is important to update the session_state
    edited_data = st.data_editor(
        display_data,
        column_config={
            "Selected": st.column_config.CheckboxColumn(
                "Upload?",
                help="Select to upload this payslip to Google Drive",
                default=False,
            ),
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

    # Update session_state.processed_payslips_data based on editor changes
    for i, row in enumerate(edited_data):
        if i < len(st.session_state.processed_payslips_data):
            st.session_state.processed_payslips_data[i]['selected_for_upload'] = row['Selected']

    # Separate Tabs for actions
    tab_drive, tab_download = st.tabs(["☁️ Google Drive Actions", "💻 Local Download"])

    # -------- Google Drive Upload Tab --------
    with tab_drive:
        if st.session_state.user_prefs.get("enable_drive_upload", True):
            selected_for_upload = [item for item in st.session_state.processed_payslips_data if item['selected_for_upload']]
            st.info(f"You have **{len(selected_for_upload)}** payslips selected for Google Drive upload.")

            if st.button("⬆️ Upload Selected to Google Drive", key="upload_selected_button",
                         disabled=not selected_for_upload):
                service = authenticate_google_drive()
                if service:
                    progress_text = "Uploading payslips to Google Drive. Please wait."
                    upload_progress_bar = st.progress(0, text=progress_text)

                    total_to_upload = len(selected_for_upload)
                    uploaded_count = 0
                    
                    for i, item in enumerate(selected_for_upload):
                        key = item['key']
                        filename = item['filename']
                        file_bytes = item['file_bytes']

                        # Check if already uploaded in this session or previously
                        if key in st.session_state.uploaded_file_keys_log:
                            item['upload_status_detail'] = "Skipped (Already Logged)"
                            uploaded_count += 1 # Count as processed for progress bar
                            upload_progress_bar.progress(uploaded_count / total_to_upload, text=f"{progress_text} ({filename}: Skipped)")
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
                        upload_progress_bar.progress(uploaded_count / total_to_upload, text=f"{progress_text} ({filename}: {item['upload_status_detail']})")

                    # Update the main editor display after upload
                    # This relies on Streamlit rerunning and the data_editor picking up changes
                    st.session_state.payslip_selection_editor = edited_data # Force refresh if needed
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

    # -------- Local Download Tab --------
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
                st.download_button(
                    "⬇️ Download Matched Payslips (ZIP)",
                    data=zip_buffer,
                    file_name="Matched_Payslips.zip",
                    mime="application/zip",
                    key="download_matched_zip"
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
                    mime="application/zip",
                    key="download_all_zip"
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
                UPLOAD_LOG = "uploaded_files.json"
                with open(UPLOAD_LOG, "w") as f:
                    json.dump([], f)
                st.session_state.uploaded_file_keys_log = set() # Also clear session state log
                st.sidebar.success("Upload log has been reset.")
            except Exception as e:
                st.sidebar.error(f"Failed to reset log: {e}")

        # Display current log from session state
        st.sidebar.info(f"📊 {len(st.session_state.uploaded_file_keys_log)} files currently logged as uploaded.")
        if st.sidebar.checkbox("📂 Show Upload Log", key="show_upload_log_admin"):
            st.sidebar.write(list(st.session_state.uploaded_file_keys_log)) # Display as list for readability
    else:
        st.sidebar.info("👤 Standard user mode (Admin tools hidden)")
