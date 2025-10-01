import streamlit as st
import re
import os
import io
import json
import zipfile
import tempfile
import base64
from PyPDF2 import PdfReader, PdfWriter

# --- Persistent User Preferences ---
PREF_FILE = "user_prefs.json"

# Load preferences if file exists
if "user_prefs" not in st.session_state:
    if os.path.exists(PREF_FILE):
        with open(PREF_FILE, "r") as f:
            st.session_state.user_prefs = json.load(f)
    else:
        # Default preferences
        st.session_state.user_prefs = {
            "drive_folder": "your-default-folder-id",
            "enable_drive_upload": True,
            "enable_local_download": True,
            "naming_pattern": "{year}_{month}_{ippis}.pdf",
            "timezone": "Africa/Lagos",
            "date_format": "YYYY-MM-DD"
        }

# Sidebar UI bound to session_state
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

# Save preferences immediately
with open(PREF_FILE, "w") as f:
    json.dump(st.session_state.user_prefs, f)

# Google Drive
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- Streamlit Page Config ---
st.set_page_config(
    page_title="ARMTI Payslip Portal",
    page_icon="assets/ARMTI.png",
    layout="wide"
)

# --- Custom Styling ---
st.markdown("""
<style>
.main-title {
    font-size: 2rem;
    font-weight: bold;
    color: #2E86C1;
    text-align: center;
    margin-bottom: 20px;
}
.stButton button {
    background-color: #2E86C1;
    color: white;
    border-radius: 8px;
    padding: 0.5em 1em;
    font-weight: bold;
}
.stButton button:hover {
    background-color: #1B4F72;
}
</style>
""", unsafe_allow_html=True)

# Convert logo to base64 so it embeds cleanly
def get_base64_of_bin_file(bin_file):
    with open(bin_file, 'rb') as f:
        data = f.read()
    return base64.b64encode(data).decode()

logo_base64 = get_base64_of_bin_file("assets/ARMTI.png")

st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@700&display=swap');

    .app-title {{
        font-family: 'Montserrat', sans-serif;
        color: #2E86C1;
        font-size: 2.4rem;
        font-weight: bold;
        margin: 0;
        padding: 0;
    }}

    .top-banner {{
        text-align: center;
        margin-top: -40px; /* moves everything up */
        margin-bottom: 15px;
    }}
    </style>

    <div class="top-banner">
        <img src="data:image/png;base64,{logo_base64}" width="110" style="margin-bottom:8px;">
        <h1 class="app-title">ARMTI PAYSLIP MANAGER</h1>
    </div>
    """,
    unsafe_allow_html=True
)

# --- Google Drive Config ---
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']
GOOGLE_DRIVE_FOLDER_ID = st.secrets["google_drive_folder_id"]

def authenticate_google_drive():
    """Authenticate with Google Drive using Streamlit secrets."""
    try:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=SCOPES
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        st.error(f"Google Drive authentication failed: {e}")
        return None

def upload_file_to_google_drive(service, filename, file_bytes, mime_type="application/pdf"):
    """Upload a file to Google Drive."""
    file_metadata = {"name": filename, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    try:
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True
        ).execute()
        st.success(f"✅ Uploaded {filename} (ID: {file.get('id')})")
    except Exception as e:
        st.error(f"❌ Failed to upload {filename}: {e}")

def get_details_from_text(text):
    """Extract Year, Month, and IPPIS Number from payslip text."""
    try:
        year_match = re.search(r'\b(20\d{2})\b', text)
        year = year_match.group(1) if year_match else None

        month_abbr_match = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{4}\b', text, re.IGNORECASE)
        month = None
        if month_abbr_match:
            month_abbr = month_abbr_match.group(1).upper()
            month_map = {
                'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
            }
            month = month_map.get(month_abbr)

        ippis_match = re.search(r'IPPIS\s*Number:\s*(\w+)', text, re.IGNORECASE)
        ippis_number = ippis_match.group(1) if ippis_match else None

        if year and month and ippis_number:
            return {'year': year, 'month': month, 'ippis_number': ippis_number}
        return None
    except Exception:
        return None

def split_and_rename_pdf(input_pdf_file):
    """Split and rename PDF pages."""
    all_processed_files = [] # (identity_key, filename, file_bytes)
    matched_files_with_keys = [] # (identity_key, filename, file_bytes)
    try:
        reader = PdfReader(input_pdf_file)
        progress = st.progress(0)

        for i, page in enumerate(reader.pages):
            writer = PdfWriter()
            writer.add_page(page)
            text = page.extract_text()
            details = get_details_from_text(text)

            identity_key = None
            new_filename = None

            if details:
                # Canonical identity key (e.g., "2023_01_12345") - NOT dependent on naming_pattern
                identity_key = f"{details['year']}_{details['month']}_{details['ippis_number']}"
                
                # Filename generated using the user's naming_pattern preference
                new_filename = st.session_state.user_prefs["naming_pattern"].format(
                    year=details["year"], month=details["month"], ippis=details["ippis_number"]
                ) + ".pdf" # Explicitly add .pdf extension
            else:
                new_filename = f"page_{i + 1}_missing_details.pdf"
                identity_key = f"page_{i + 1}_no_details" # Fallback key for non-matched files

            buffer = io.BytesIO()
            writer.write(buffer)
            buffer.seek(0)
            file_bytes = buffer.read()

            all_processed_files.append((identity_key, new_filename, file_bytes))
            if details: # Only append to matched_files_with_keys if details were found
                matched_files_with_keys.append((identity_key, new_filename, file_bytes))

            progress.progress((i + 1) / len(reader.pages))

        st.success("✅ All pages processed successfully!")
        progress.empty()
        return all_processed_files, matched_files_with_keys

    except Exception as e:
        st.error(f"Error while processing PDF: {e}")
        return [], []

# --- Instructions ---
st.markdown("""
Upload a multi-page PDF containing payslips, and this app will split each page into a separate PDF
and rename it based on the Year, Month, and IPPIS Number found in the payslip text.
""")

# --- Main File Uploader ---
uploaded_file = st.file_uploader("📂 Upload a PDF containing payslips", type="pdf", help="Drag & drop or click to browse")

if uploaded_file:
    st.success("File uploaded successfully!")

    if st.button("🚀 Split & Process Payslips"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name

        all_pdfs, matched_pdfs = split_and_rename_pdf(tmp_file_path)

        if all_pdfs:
            tab1, tab2 = st.tabs(["☁️ Google Drive Upload", "💻 Local Download"])

            with tab1:
                if st.session_state.user_prefs["enable_drive_upload"]:
                    service = authenticate_google_drive()
                    if service and matched_pdfs_with_keys: # Ensure you're using matched_pdfs_with_keys
                        st.info(f"Found {len(matched_pdfs_with_keys)} potential payslips to upload.")

                        UPLOAD_LOG = "uploaded_files.json"
                        if os.path.exists(UPLOAD_LOG):
                            with open(UPLOAD_LOG, "r") as f:
                                uploaded_file_keys = set(json.load(f))
                        else:
                            uploaded_file_keys = set()

                        # --- Initialize status table ---
                        status_data = []
                        # Corrected: Iterate over (key, filename, file_bytes)
                        for key, filename, file_bytes in matched_pdfs_with_keys:
                            if key in uploaded_file_keys:
                                status_data.append({"filename": filename, "status": "⏩ Skipped"})
                            else:
                                status_data.append({"filename": filename, "status": "⏳ Pending"})

                        status_placeholder = st.empty()
                        status_placeholder.table(status_data)
                        progress_bar = st.progress(0)

                        total = len(matched_pdfs_with_keys)
                        completed = 0
                        new_uploads = 0

                        # The main upload loop, which I believe was already corrected
                        for key, filename, file_bytes in matched_pdfs_with_keys:
                            if key in uploaded_file_keys:
                                completed += 1
                                progress_bar.progress(completed / total)
                                # Update status table for skipped items already marked pending
                                for row in status_data:
                                    if row["filename"] == filename and row["status"] == "⏳ Pending":
                                        row["status"] = "⏩ Skipped" # Mark it explicitly as skipped
                                        break
                                status_placeholder.table(status_data)
                                continue

                            # Mark as uploading
                            for row in status_data:
                                if row["filename"] == filename:
                                    row["status"] = "🔄 Uploading"
                                    break
                            status_placeholder.table(status_data)

                            try:
                                upload_file_to_google_drive(service, filename, file_bytes)
                                for row in status_data:
                                    if row["filename"] == filename:
                                        row["status"] = "✅ Uploaded"
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

                        with open(UPLOAD_LOG, "w") as f:
                            json.dump(list(uploaded_file_keys), f)

                        st.info(f"Upload complete. {new_uploads} new files uploaded, {total - new_uploads} skipped.")


            with tab2:
                # This 'if' statement and its content must be indented under 'with tab2:'
                if st.session_state.user_prefs["enable_local_download"]:
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
