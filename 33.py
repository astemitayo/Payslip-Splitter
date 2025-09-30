import streamlit as st
import os
import re
from PyPDF2 import PdfReader, PdfWriter
import tempfile
import zipfile
import io # Needed for file-like objects for upload

# Google Drive imports
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- Configuration for Google Drive ---
# IMPORTANT: Replace with the actual ID of your Google Drive folder
# Ensure this folder is within a Google Shared Drive and your service account has editor/contributor access.
GOOGLE_DRIVE_FOLDER_ID = st.secrets["google_drive_folder_id"]
SERVICE_ACCOUNT_FILE = "service_account.json" # Make sure this file is in the same directory

# Scopes define what your app can do on Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive'] # drive.file for files created by the app, drive for broader access


def authenticate_google_drive():
    """Authenticates with Google Drive using a service account stored in Streamlit secrets."""
    try:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=SCOPES
        )
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception as e:
        st.error(f"Error authenticating with Google Drive: {e}")
        st.error("Check your Streamlit secrets configuration.")
        return None

def upload_file_to_google_drive(service, filename, file_bytes, mime_type):
    """Uploads a file to Google Drive."""
    if not service:
        st.error("Google Drive service not initialized.")
        return False

    file_metadata = {
        'name': filename,
        'parents': [GOOGLE_DRIVE_FOLDER_ID]
    }
    # Add support for Shared Drives if the folder is one
    # If the folder is a Shared Drive, 'supportsAllDrives' must be set to True
    # The API will automatically determine if it's a Shared Drive folder
    # but explicitly setting this can sometimes help.
    parameters = {'supportsAllDrives': True}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    try:
        # Pass supportsAllDrives parameter for create method
        file = service.files().create(body=file_metadata, media_body=media, fields='id', **parameters).execute()
        st.success(f"Uploaded '{filename}' to Google Drive. File ID: {file.get('id')}")
        return True
    except Exception as e:
        st.error(f"Failed to upload '{filename}' to Google Drive: {e}")
        st.error("Hint: If you see a 'storage quota' error, ensure GOOGLE_DRIVE_FOLDER_ID points to a folder in a Shared Drive and your service account has appropriate permissions.")
        return False


def get_details_from_text(text):
    """
    Extracts the Year, Month, and IPPIS Number from the text of a payslip.
    This version is specifically tailored to your payslip format.
    """
    try:
        year_match = re.search(r'\b(20\d{2})\b', text)
        year = year_match.group(1) if year_match else None

        month_abbr_match = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{4}\b', text, re.IGNORECASE)
        if month_abbr_match:
            month_abbr = month_abbr_match.group(1).upper()
            month_map = {
                'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
            }
            month = month_map.get(month_abbr)
        else:
            month = None

        ippis_match = re.search(r'IPPIS\s*Number:\s*(\w+)', text, re.IGNORECASE)
        ippis_number = ippis_match.group(1) if ippis_match else None

        if year and month and ippis_number:
            return {'year': year, 'month': month, 'ippis_number': ippis_number}
        return None
    except Exception as e:
        st.error(f"An error occurred while extracting details: {e}")
        return None

def split_and_rename_pdf_webapp(input_pdf_file):
    """
    Splits and renames each page of the PDF for the web app.
    Returns a list of tuples: (filename, bytes_data) for all processed PDFs
    and a filtered list containing only successfully named PDFs.
    """
    all_processed_files_data = [] # Store (filename, bytes_data) directly for immediate use
    matched_files_data = [] # Store only successfully named files
    
    try:
        reader = PdfReader(input_pdf_file)
        st.write("Processing PDF...")

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

            # Get bytes directly into a buffer
            buffer = io.BytesIO()
            writer.write(buffer)
            buffer.seek(0)
            file_bytes = buffer.read()
            
            all_processed_files_data.append((new_filename, file_bytes))
            if is_matched:
                matched_files_data.append((new_filename, file_bytes))
            
            st.write(f"Processed page {i+1}: {new_filename}")

        st.success("TASK COMPLETE: All pages have been split and renamed.")
        return all_processed_files_data, matched_files_data

    except Exception as e:
        st.error(f"An unexpected error occurred during PDF processing: {e}")
        return [], []


st.set_page_config(layout="wide")
st.title("ARMTI Payslip Manager")
st.markdown("""
Upload a multi-page PDF containing payslips, and this app will split each page into a separate PDF
and rename it based on the Year, Month, and IPPIS Number found in the payslip text.
""")

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")

if uploaded_file is not None:
    st.write("File uploaded successfully!")
    
    if st.button("Split and Rename Payslips"):
        # Use a temporary file to save the uploaded PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name
        
        all_processed_pdfs_data, matched_pdfs_data = split_and_rename_pdf_webapp(tmp_file_path)

        if all_processed_pdfs_data:
            # --- Google Drive Upload Section ---
            st.subheader("Google Drive Upload:")
            st.warning("IMPORTANT: Ensure 'GOOGLE_DRIVE_FOLDER_ID' points to a folder within a Google Shared Drive and your service account has at least 'Content Manager' or 'Contributor' permissions to avoid 'storage quota' errors.")
            google_drive_service = authenticate_google_drive()
            
            if google_drive_service:
                if matched_pdfs_data: # Only show upload options if there are matched files
                    st.info(f"Found {len(matched_pdfs_data)} files matching the naming pattern for upload.")
                    
                    # Removed the radio button, directly proceed with individual uploads
                    st.info("Uploading individual matched PDF files to Google Drive...")
                    for filename, file_bytes in matched_pdfs_data: # Use matched_pdfs_data here
                        upload_file_to_google_drive(google_drive_service, filename, file_bytes, 'application/pdf')
                else:
                    st.warning("No files found that matched the naming pattern. Skipping Google Drive upload.")
            else:
                st.warning("Google Drive upload skipped due to authentication failure.")


            # --- Local Download Section ---
            st.subheader("Local Downloads:")
            
            # Option for downloading only matched files as a single ZIP locally
            if matched_pdfs_data:
                st.write("Download only successfully named payslips:")
                zip_buffer_local_matched = io.BytesIO()
                with zipfile.ZipFile(zip_buffer_local_matched, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for filename, file_bytes in matched_pdfs_data:
                        zf.writestr(filename, file_bytes)
                zip_buffer_local_matched.seek(0)
                st.download_button(
                    label="Download Matched Payslips as ZIP (Local)",
                    data=zip_buffer_local_matched.read(),
                    file_name="Matched_Payslips.zip",
                    mime="application/zip"
                )
                zip_buffer_local_matched.close()
            else:
                st.info("No files found that matched the naming pattern for local download.")

            # Option for downloading ALL processed files (including those with missing details)
            if all_processed_pdfs_data:
                st.markdown("---") # Separator
                st.write("Download ALL processed files (including those with 'missing_details'):")
                zip_buffer_local_all = io.BytesIO()
                with zipfile.ZipFile(zip_buffer_local_all, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for filename, file_bytes in all_processed_pdfs_data:
                        zf.writestr(filename, file_bytes)
                zip_buffer_local_all.seek(0)
                st.download_button(
                    label="Download All Processed Payslips as ZIP (Local)",
                    data=zip_buffer_local_all.read(),
                    file_name="All_Processed_Payslips.zip",
                    mime="application/zip"
                )
                zip_buffer_local_all.close()

        # Clean up the temporary uploaded file
        os.remove(tmp_file_path)

st.sidebar.header("About")
st.sidebar.info("This app uses Python and Streamlit to automate payslip processing and optionally upload to Google Drive.")
