import streamlit as st
import re
import tempfile
import zipfile
import io
from PyPDF2 import PdfReader, PdfWriter

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

import base64

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

def upload_file_to_google_drive(service, filename, file_bytes, mime_type):
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
    all_processed_files = []
    matched_files = []
    try:
        reader = PdfReader(input_pdf_file)
        progress = st.progress(0)

        for i, page in enumerate(reader.pages):
            writer = PdfWriter()
            writer.add_page(page)
            text = page.extract_text()
            details = get_details_from_text(text)

            is_matched = False
            if details:
                new_filename = f"{details['year']} {details['month']} {details['ippis_number']}.pdf"
                is_matched = True
            else:
                new_filename = f"page_{i + 1}_missing_details.pdf"

            buffer = io.BytesIO()
            writer.write(buffer)
            buffer.seek(0)
            file_bytes = buffer.read()

            all_processed_files.append((new_filename, file_bytes))
            if is_matched:
                matched_files.append((new_filename, file_bytes))

            progress.progress((i + 1) / len(reader.pages))

        st.success("✅ All pages processed successfully!")
        progress.empty()
        return all_processed_files, matched_files

    except Exception as e:
        st.error(f"Error while processing PDF: {e}")
        return [], []

# --- Sidebar Options ---
st.sidebar.title("⚙️ Options")
enable_drive_upload = st.sidebar.checkbox("Upload to Google Drive", value=True)
enable_local_download = st.sidebar.checkbox("Download Locally", value=True)

st.set_page_config(layout="wide")
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
    if enable_drive_upload:
        service = authenticate_google_drive()
        if service and matched_pdfs:
            st.info(f"Found {len(matched_pdfs)} valid payslips to upload.")

            # --- Duplicate skip log ---
            UPLOAD_LOG = "uploaded_files.json"
            if os.path.exists(UPLOAD_LOG):
                with open(UPLOAD_LOG, "r") as f:
                    uploaded_files = set(json.load(f))
            else:
                uploaded_files = set()

            new_uploads = 0
            for filename, file_bytes in matched_pdfs:
                if filename in uploaded_files:
                    st.warning(f"⏩ Skipped {filename} (already uploaded)")
                    continue  # skip duplicate

                try:
                    upload_file_to_google_drive(service, filename, file_bytes, "application/pdf")
                    uploaded_files.add(filename)
                    new_uploads += 1
                    st.success(f"✅ Uploaded {filename}")
                except Exception as e:
                    st.error(f"❌ Failed to upload {filename}: {e}")

            # save updated log
            with open(UPLOAD_LOG, "w") as f:
                json.dump(list(uploaded_files), f)

            st.info(f"Upload complete. {new_uploads} new files uploaded, {len(matched_pdfs)-new_uploads} skipped.")
        elif not matched_pdfs:
            st.warning("No valid payslips found for upload.")

                    elif not matched_pdfs:
                        st.warning("No valid payslips found for upload.")

            with tab2:
                if enable_local_download:
                    if matched_pdfs:
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                            for filename, file_bytes in matched_pdfs:
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
                        for filename, file_bytes in all_pdfs:
                            zf.writestr(filename, file_bytes)
                    zip_buffer_all.seek(0)
                    st.download_button(
                        "⬇️ Download All Processed Payslips (ZIP)",
                        data=zip_buffer_all,
                        file_name="All_Processed_Payslips.zip",
                        mime="application/zip"
                    )
