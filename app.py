# app.py
import streamlit as st
import os
import re
import io
import json
import csv
import tempfile
import mimetypes
import zipfile
from datetime import datetime, timezone
from PyPDF2 import PdfReader, PdfWriter

# Google auth & Drive
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------- Configuration ----------------
GOOGLE_DRIVE_FOLDER_ID = "1CDY8S1nZ8V_46AgnH-QRseXdJjvNtMtB"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.pickle"

PROGRESS_LOG = "progress_log.json"
SUMMARY_JSON = "Run_Summary.json"
SUMMARY_CSV = "Run_Summary.csv"

SCOPES = ['https://www.googleapis.com/auth/drive.file']


# ---------------- Auth ----------------
def authenticate_google_drive():
    # Load credentials JSON from Streamlit Cloud secrets (or environment variable)
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    
    # Save creds to a temporary file (since the Google lib requires a file path)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(json.dumps(creds_dict).encode("utf-8"))
        tmp_path = tmp.name

    flow = InstalledAppFlow.from_client_secrets_file(tmp_path, SCOPES)
    creds = flow.run_local_server(port=0)
    return creds


def find_file_in_folder(service, filename, folder_id):
    safe_name = filename.replace("'", "\\'")
    q = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
    res = service.files().list(q=q, spaces='drive', fields='files(id,name)', pageSize=1).execute()
    files = res.get('files', [])
    return files[0] if files else None


def upload_or_overwrite(service, filename, file_bytes, mime_type, folder_id, overwrite=False):
    file_metadata = {'name': filename, 'parents': [folder_id]}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    existing = find_file_in_folder(service, filename, folder_id)
    if existing:
        if overwrite:
            updated = service.files().update(fileId=existing['id'], media_body=media, fields='id').execute()
            return ("overwritten", updated.get('id'))
        else:
            return ("skipped", existing['id'])
    else:
        created = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return ("uploaded", created.get('id'))


# ---------------- PDF Processing ----------------
def get_details_from_text(text):
    year_match = re.search(r'\b(20\d{2})\b', text)
    year = year_match.group(1) if year_match else None
    month_abbr_match = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{4}\b', text, re.IGNORECASE)
    if month_abbr_match:
        month_map = {'JAN':'01','FEB':'02','MAR':'03','APR':'04','MAY':'05','JUN':'06',
                     'JUL':'07','AUG':'08','SEP':'09','OCT':'10','NOV':'11','DEC':'12'}
        month = month_map.get(month_abbr_match.group(1).upper())
    else:
        month = None
    ippis_match = re.search(r'IPPIS\s*Number:\s*(\w+)', text, re.IGNORECASE)
    ippis_number = ippis_match.group(1) if ippis_match else None
    if year and month and ippis_number:
        return {'year': year, 'month': month, 'ippis_number': ippis_number}
    return None


def split_and_rename_pdf(input_pdf_path):
    all_files, matched_files = [], []
    reader = PdfReader(input_pdf_path)
    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        text = page.extract_text() or ""
        details = get_details_from_text(text)
        if details:
            filename = f"{details['year']} {details['month']} {details['ippis_number']}.pdf"
            is_matched = True
        else:
            filename = f"page_{i+1}_missing_details.pdf"
            is_matched = False
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        file_bytes = buf.read()
        all_files.append((filename, file_bytes))
        if is_matched:
            matched_files.append((filename, file_bytes))
    return all_files, matched_files


# ---------------- Progress & Summary ----------------
def load_progress():
    if os.path.exists(PROGRESS_LOG):
        with open(PROGRESS_LOG, "r") as f:
            return json.load(f)
    return {}


def save_progress(log):
    with open(PROGRESS_LOG, "w") as f:
        json.dump(log, f, indent=2)


def save_summary(rows, filename="summary.csv"):
    fieldnames = set()
    dict_rows = []

    for row in rows:
        if isinstance(row, dict):
            fieldnames.update(row.keys())
            dict_rows.append(row)
        else:
            # Log and skip anything that isn’t a dictionary
            print(f"⚠️ Skipping non-dict row: {row}")

    if not dict_rows:
        print("⚠️ No valid dictionary rows found. Nothing to save.")
        return

    fieldnames = list(fieldnames)

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(dict_rows)

    print(f"✅ Summary saved to {filename} with {len(dict_rows)} rows.")



# ---------------- Streamlit UI ----------------
st.set_page_config(layout="wide")
st.title("Payslip PDF Splitter with Resumable Upload & Summary")

overwrite_toggle = st.sidebar.checkbox("Overwrite existing files", value=False)

uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")

if uploaded_file:
    st.success(f"Uploaded: {uploaded_file.name}")

    if st.button("Process & Upload"):
        progress = load_progress()
        source_key = f"{uploaded_file.name}::{len(uploaded_file.getvalue())}"
        if source_key not in progress:
            progress[source_key] = {"processed": {}}

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name

        all_files, matched_files = split_and_rename_pdf(tmp_path)
        service = authenticate_google_drive()

        summary = {"details": {"uploaded":[],"overwritten":[],"skipped":[],"failed":[]}}

        # Progress bar
        progress_bar = st.progress(0)
        total = len(matched_files)
        for idx, (filename, file_bytes) in enumerate(matched_files, 1):
            if filename in progress[source_key]["processed"]:
                st.write(f"Skipping (already done): {filename}")
                summary["details"]["skipped"].append({"filename": filename, "reason":"progress log"})
                progress_bar.progress(idx / total)
                continue
            try:
                mime_type = mimetypes.guess_type(filename)[0] or "application/pdf"
                status, file_id = upload_or_overwrite(
                    service, filename, file_bytes, mime_type,
                    GOOGLE_DRIVE_FOLDER_ID, overwrite_toggle
                )
                entry = {
                    "filename": filename,
                    "file_id": file_id,
                    "time": datetime.now(timezone.utc).isoformat()
                }
                summary["details"][status].append(entry)
                progress[source_key]["processed"][filename] = status
                save_progress(progress)
                st.write(f"{filename} -> {status}")
            except Exception as e:
                summary["details"]["failed"].append({
                    "filename": filename,
                    "error": str(e)
                })
                st.error(f"Failed {filename}: {e}")
            progress_bar.progress(idx / total)

        counts = {k:len(v) for k,v in summary["details"].items()}
        summary["meta"] = {
            "source": uploaded_file.name,
            "counts": counts,
            "time": datetime.now(timezone.utc).isoformat()
        }
        save_summary(summary)

        st.subheader("Run Summary")
        st.json(summary)
        with open(SUMMARY_JSON,"rb") as f: 
            st.download_button("Download JSON", f, file_name=SUMMARY_JSON)
        with open(SUMMARY_CSV,"rb") as f: 
            st.download_button("Download CSV", f, file_name=SUMMARY_CSV)

        # Local ZIP download
        if matched_files:
            st.subheader("Download Matched Files (ZIP)")
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for filename, file_bytes in matched_files:
                    zf.writestr(filename, file_bytes)
            zip_buf.seek(0)
            st.download_button(
                "Download Matched Payslips as ZIP",
                data=zip_buf.read(),
                file_name="Matched_Payslips.zip",
                mime="application/zip"
            )
else:
    st.info("Please upload a PDF to begin.")
