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
# Text/detail extraction utils
# -----------------------------
# --- Step 1: Convert all pages to images and OCR them ---
input_pdf = []
reader = PdfReader(input_pdf)
page_count = len(reader.pages)

ocr_texts = []
for i in range(page_count):
    # Convert only one page at a time to control memory use
    pages = convert_from_path(
        input_pdf, dpi=150, first_page=i + 1, last_page=i + 1, poppler_path=poppler_path
    )
    text = pytesseract.image_to_string(pages[0])
    ocr_texts.append(text)

    # Print progress every 10 pages
    if (i + 1) % 10 == 0 or (i + 1) == page_count:
        print(f"   ... processed {i + 1}/{page_count} pages")
        
# --- Step 2: Group pages into payslips ---
print("📄 Grouping pages into individual payslips...")

payslip_groups = []
current_group = []

for i, text in enumerate(ocr_texts):
    text_upper = text.upper()

    # Start of new payslip
    if "FEDERAL GOVERNMENT OF NIGERIA" in text_upper:
        if current_group:
            payslip_groups.append(current_group)
            current_group = []
    current_group.append(i)

    # End of payslip
    if "TOTAL NET EARNINGS" in text_upper:
        payslip_groups.append(current_group)
        current_group = []

if current_group:
    payslip_groups.append(current_group)

print(f"✅ Detected {len(payslip_groups)} payslips.\n")

# --- Step 3: Extract info and save each payslip ---
month_map = {
    'JANUARY': '01', 'FEBRUARY': '02', 'MARCH': '03', 'APRIL': '04',
    'MAY': '05', 'JUNE': '06', 'JULY': '07', 'AUGUST': '08',
    'SEPTEMBER': '09', 'OCTOBER': '10', 'NOVEMBER': '11', 'DECEMBER': '12'
}

for idx, group in enumerate(payslip_groups, start=1):
    writer = PdfWriter()
    merged_text = ""

    for pg in group:
        writer.add_page(reader.pages[pg])
        merged_text += ocr_texts[pg] + "\n"

    # --- Extract metadata ---
    year_match = re.search(r'\b(20\d{2})\b', merged_text)
    month_match = re.search(
        r'\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\b',
        merged_text, re.IGNORECASE
    )

    # --- Robust IPPIS detection ---
    ippis_match = None
    m1 = re.search(r'IPPIS\s*Number[:\-]?\s*(\d{3,})', merged_text, re.IGNORECASE)
    m2 = re.search(r'(\d{3,})\s*Step', merged_text, re.IGNORECASE)
    m3 = re.search(r'FGN\s+CIVIL\s+SERVICE.*?(\d{5,6})', merged_text, re.IGNORECASE | re.DOTALL)
    m4 = re.search(r'FEDERAL\s+GOVERNMENT.*?(\d{5,6})', merged_text, re.IGNORECASE | re.DOTALL)
    m5 = re.search(r'\b(\d{6})\b', merged_text)

    ippis_match = m1 or m2 or m3 or m4 or m5

    total_net_match = re.search(
        r'Total\s+Net\s+Earnings[:\-]?\s*N?([\d,]+\.\d{2})',
        merged_text, re.IGNORECASE
    )

    year = year_match.group(1) if year_match else "UnknownYear"
    month = month_map.get(month_match.group(1).upper(), "00") if month_match else "00"
    ippis = ippis_match.group(1) if ippis_match else f"UnknownIPPIS{idx}"

    # --- Filename format: YYYY MM IPPISNUMBER ---
    base_filename = f"{year} {month} {ippis}"
    filename = f"{base_filename}.pdf"
    filepath = os.path.join(output_folder, filename)

    # Ensure uniqueness
    counter = 1
    while os.path.exists(filepath):
        filename = f"{base_filename} {counter}.pdf"
        filepath = os.path.join(output_folder, filename)
        counter += 1

    # --- Write the individual payslip ---
    with open(filepath, "wb") as f:
        writer.write(f)

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
        # Start marker heuristic - Look for "FEDERAL GOVERNMENT OF NIGERIA" or "PAYSLIP"
        # and ensure it's not the first page if we are starting a new group
        # This prevents breaking a multi-page payslip that starts with a header on page 1
        is_new_payslip_header = ("FEDERAL GOVERNMENT OF NIGERIA" in tu or "PAYSLIP" in tu)
        if is_new_payslip_header and current and i not in current: # Only start a new group if current is not empty and it's not the same first page
            groups.append(current)
            current = []
        current.append(i)
        # End marker heuristics
        if any(k in tu for k in ("TOTAL NET EARNINGS", "NET PAY", "NET SALARY", "NET EARNINGS")):
            # If an end marker is found, this group is complete.
            # Add it, and clear current for the next potential payslip.
            groups.append(current)
            current = []
    if current: # Add any remaining pages as a final group
        groups.append(current)

    # If grouping produced only trivial singletons or no markers found, fall back to per-page grouping
    # This also helps if the PDF has no clear markers but each page is a payslip
    if not groups or all(len(g) == 1 for g in groups) and len(groups) == len(texts):
        st.info("No strong payslip markers found for intelligent grouping. Falling back to treating each page as a potential payslip.")
        return [[i] for i in range(len(texts))]

    return groups


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
        # Write bytes to a temp file for pdf2image (which needs a file path)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(input_pdf_bytes)
            tmp_path = tmp_file.name

        reader = PdfReader(io.BytesIO(input_pdf_bytes))
        num_pages = len(reader.pages)
        
        # Build page_texts according to mode
        page_texts = []
        st.toast(f"Extracting text from {num_pages} page(s) in {ocr_mode} mode...", icon="📄")

        if ocr_mode == "Full OCR":
            page_texts = extract_all_pages_ocr(input_pdf_bytes)

        else:
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

        page_groups = group_pages_by_payslip_from_texts(page_texts)

        st.toast(f"Found {len(page_groups)} potential payslip documents after grouping.", icon="✂️")

        for g_index, group in enumerate(page_groups, start=1):
            writer = PdfWriter()
            merged_text = ""
            for pg in group:
                writer.add_page(reader.pages[pg])
                merged_text += (page_texts[pg] or "") + "\n"

            details = get_details_from_text(merged_text)
            
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
                payslip_info['key'] = f"{details['year']}_{details['month']}_{details['ippis_number']}_{g_index}" # Added g_index to key to ensure uniqueness if details are same but from different groups
                payslip_info['filename'] = naming_pattern.format(year=details["year"], month=details["month"], ippis=details["ippis_number"])
                if not payslip_info['filename'].lower().endswith(".pdf"):
                    payslip_info['filename'] += ".pdf"
                payslip_info['status'] = "Details Extracted"
            else:
                payslip_info['key'] = f"no_details_group_{g_index}_from_{os.path.basename(tmp_path)}"
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
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

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
