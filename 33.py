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
            "naming_pattern": "{year} {month} {ippis}", # Changed default to reflect your preference
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
    page_icon="assets/ARMTI.png", # Ensure assets/ARMTI.png exists
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
    if not os.path.exists(bin_file):
        st.error(f"Error: Asset file not found at {bin_file}. Please ensure 'assets/ARMTI.png' exists.")
        # Return a placeholder or raise an error depending on desired behavior
        return "" # Return empty string to prevent further errors
    with open(bin_file, 'rb') as f:
        data = f.read()
    return base64.b64encode(data).decode()

# Ensure you have an 'assets' folder with 'ARMTI.png'
# If not, this line might cause an error or display a broken image
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
# It's better to get this from secrets as well, similar to gcp_service_account
# GOOGLE_DRIVE_FOLDER_ID = st.secrets["google_drive_folder_id"] # Uncomment this if it's in secrets.toml
# For now, let's use the default from preferences if not found in secrets
try:
    GOOGLE_DRIVE_FOLDER_ID = st.secrets["google_drive_folder_id"]
except KeyError:
    GOOGLE_DRIVE_FOLDER_ID = st.session_state.user_prefs["drive_folder"]
    st.warning("`google_drive_folder_id` not found in `st.secrets`. Using folder ID from preferences.")


def authenticate_google_drive():
    """Authenticate with Google Drive using Streamlit secrets."""
    try:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=SCOPES
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        st.error(f"Google Drive authentication failed: {e}. Make sure 'gcp_service_account' is correctly set in .streamlit/secrets.toml")
        return None

def upload_file_to_google_drive(service, filename, file_bytes, mime_type="application/pdf"):
    """Upload a file to Google Drive."""
    # Use the GOOGLE_DRIVE_FOLDER_ID determined at the top
    file_metadata = {"name": filename, "parents": [GOOGLE_DRIVE_FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    try:
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True # Important for shared drives
        ).execute()
        st.success(f"✅ Uploaded {filename} (ID: {file.get('id')})")
    except Exception as e:
        st.error(f"❌ Failed to upload {filename}: {e}")

def get_details_from_text(text):
    """Extract Year, Month, and IPPIS Number from payslip text."""
    try:
        year_match = re.search(r'\b(20\d{2})\b', text)
        year = year_match.group(1) if year_match else None

        # This regex looks for MON-YYYY (e.g., JAN-2023). If your payslips use a different format, adjust this.
        month_abbr_match = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{4}\b', text, re.IGNORECASE)
        month = None
        if month_abbr_match:
            month_abbr = month_abbr_match.group(1).upper()
            month_map = {
                'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
            }
            month = month_map.get(month_abbr)
        # Add a fallback for month if "MON-YYYY" isn't found, maybe "MONTHNAME YYYY"
        if not month:
            full_month_match = re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b', text, re.IGNORECASE)
            if full_month_match:
                full_month_name = full_month_match.group(1).capitalize()
                month_map_full = {
                    'January': '01', 'February': '02', 'March': '03', 'April': '04', 'May': '05', 'June': '06',
                    'July': '07', 'August': '08', 'September': '09', 'October': '10', 'November': '11', 'December': '12'
                }
                month = month_map_full.get(full_month_name)
                # If year was not found by previous regex but found here
                if not year:
                    year = full_month_match.group(2)


        ippis_match = re.search(r'IPPIS\s*Number:\s*(\w+)', text, re.IGNORECASE)
        ippis_number = ippis_match.group(1) if ippis_match else None
        
        # Fallback IPPIS regex if "IPPIS Number:" isn't present but just a number is
        if not ippis_number:
            # This is very generic, make it more specific if possible (e.g., length, preceding text)
            # Example: looking for a 6-digit number, often an IPPIS is a specific length
            ippis_number_generic_match = re.search(r'\b(\d{6,10})\b', text) # Adjust length based on typical IPPIS
            if ippis_number_generic_match:
                ippis_number = ippis_number_generic_match.group(1)


        if year and month and ippis_number:
            return {'year': year, 'month': month, 'ippis_number': ippis_number}
        
        # If any part is missing, we can log which part for debugging
        # st.warning(f"Could not extract all details: Year={year}, Month={month}, IPPIS={ippis_number}")
        return None
    except Exception as e:
        # st.error(f"Error during detail extraction: {e}") # Log this if it helps debug text parsing
        return None

def split_and_rename_pdf(input_pdf_file):
    """Split and rename PDF pages."""
    all_processed_files_with_keys = [] # (identity_key, filename, file_bytes)
    matched_files_with_keys = [] # (identity_key, filename, file_bytes)
    try:
        reader = PdfReader(input_pdf_file)
        num_pages = len(reader.pages)
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
                )
                # Ensure .pdf extension is always present and only once
                if not new_filename.lower().endswith(".pdf"):
                    new_filename += ".pdf"
            else:
                new_filename = f"page_{i + 1}_missing_details.pdf"
                identity_key = f"page_{i + 1}_no_details_{os.path.basename(input_pdf_file)}" # More unique fallback key

            buffer = io.BytesIO()
            writer.write(buffer)
            buffer.seek(0)
            file_bytes = buffer.read()

            all_processed_files_with_keys.append((identity_key, new_filename, file_bytes))
            if details: # Only append to matched_files_with_keys if details were found
                matched_files_with_keys.append((identity_key, new_filename, file_bytes))

            progress.progress((i + 1) / num_pages)

        st.success("✅ All pages processed successfully!")
        progress.empty()
        return all_processed_files_with_keys, matched_files_with_keys

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

        # Assign the results to the correctly named variables
        all_pdfs_with_keys, matched_pdfs_with_keys = split_and_rename_pdf(tmp_file_path)

        # Clean up temporary file
        os.unlink(tmp_file_path)

        if all_pdfs_with_keys:
            tab1, tab2 = st.tabs(["☁️ Google Drive Upload", "💻 Local Download"])

            with tab1:
                if st.session_state.user_prefs["enable_drive_upload"]:
                    service = authenticate_google_drive()
                    if service and matched_pdfs_with_keys:
                        st.info(f"Found {len(matched_pdfs_with_keys)} potential payslips to upload.")

                        UPLOAD_LOG = "uploaded_files.json"
                        if os.path.exists(UPLOAD_LOG):
                            try:
                                with open(UPLOAD_LOG, "r") as f:
                                    uploaded_file_keys = set(json.load(f))
                            except json.JSONDecodeError:
                                st.warning("Corrupted UPLOAD_LOG file. Starting with an empty log.")
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
                                # Already marked as skipped, just update progress
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
                                upload_file_to_google_drive(service, filename, file_bytes)
                                for row in status_data:
                                    if row["filename"] == filename:
                                        row["status"] = "✅ Uploaded Successfully"
                                        break
                                uploaded_file_keys.add(key) # Add the key to the set of uploaded files
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
                        with open(UPLOAD_LOG, "w") as f:
                            json.dump(list(uploaded_file_keys), f)

                        st.info(f"Upload complete. {new_uploads} new files uploaded, {total - new_uploads} skipped.")
                    elif not service:
                         st.warning("Google Drive upload is enabled but authentication failed. Skipping upload.")
                    else: # service is None or matched_pdfs_with_keys is empty
                        st.info("No valid payslips found with extractable details for upload, or no files to upload.")


            # --- Admin Authentication ---
            st.sidebar.markdown("### 🔐 Admin Login")
            admin_pw = st.sidebar.text_input("Enter admin password", type="password")

            # Compare with secret in .streamlit/secrets.toml
            is_admin = admin_pw == st.secrets.get("admin_password", "")

            if is_admin:
                st.sidebar.success("✅ Admin access granted")

                # --- Maintenance: Upload Log ---
                st.sidebar.markdown("---")
                st.sidebar.subheader("🛠 Upload Log Maintenance")

                # Reset upload log
                if st.sidebar.button("🗑 Reset Upload Log"):
                    try:
                        with open("uploaded_files.json", "w") as f:
                            json.dump([], f)
                        st.sidebar.success("Upload log has been reset. All files will upload fresh on next run.")
                    except Exception as e:
                        st.sidebar.error(f"Failed to reset log: {e}")

                # View current upload log
                if os.path.exists("uploaded_files.json"):
                    try:
                        with open("uploaded_files.json", "r") as f:
                            uploaded_debug = json.load(f)

                        # Show count
                        st.sidebar.info(f"📊 {len(uploaded_debug)} files currently logged as uploaded.")

                        # Optional toggle to view full list
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
                    st.sidebar.info("No upload log found yet.")

            else:
                st.sidebar.info("👤 Standard user mode (Admin tools hidden)")


            with tab2:
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
                    else:
                        st.info("No payslips with extractable details were found to download locally.")

                    if all_pdfs_with_keys: # Offer download of all, even those without full details
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
                    elif not matched_pdfs_with_keys: # Only show this if no matched files at all
                         st.info("No pages were processed for local download.")
                else:
                    st.info("Local download is disabled in settings.")