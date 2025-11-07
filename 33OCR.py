import os
import io
import re
import zipfile
import tempfile
import streamlit as st
from pdf2image import convert_from_bytes
from PyPDF2 import PdfReader, PdfWriter
import pytesseract

st.set_page_config(page_title="Payslip Splitter & OCR", layout="wide")
st.title("📄 Payslip Splitter & OCR")
st.caption("Upload a combined PDF payslip file and automatically split it by staff.")

# --- Upload section ---
uploaded_file = st.file_uploader("Upload Combined Payslip PDF", type=["pdf"])

if uploaded_file is not None:
    with st.spinner("🔍 Processing PDF... please wait"):
        pdf_bytes = uploaded_file.read()
        reader = PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)

        st.write(f"✅ Loaded **{page_count}** page(s) from your PDF.")

        # --- Step 1: OCR each page ---
        ocr_texts = []
        for i, page in enumerate(reader.pages):
            images = convert_from_bytes(pdf_bytes, dpi=150, first_page=i + 1, last_page=i + 1)
            text = pytesseract.image_to_string(images[0])
            ocr_texts.append(text)

        # --- Step 2: Group pages by payslip ---
        payslip_groups = []
        current_group = []

        for i, text in enumerate(ocr_texts):
            t = text.upper()

            if "FEDERAL GOVERNMENT OF NIGERIA" in t:
                if current_group:
                    payslip_groups.append(current_group)
                    current_group = []
            current_group.append(i)

            if "TOTAL NET EARNINGS" in t:
                payslip_groups.append(current_group)
                current_group = []

        if current_group:
            payslip_groups.append(current_group)

        st.success(f"Detected **{len(payslip_groups)}** payslips.")

        # --- Step 3: Extract details and save to a ZIP file ---
        month_map = {
            'JANUARY': '01', 'FEBRUARY': '02', 'MARCH': '03', 'APRIL': '04',
            'MAY': '05', 'JUNE': '06', 'JULY': '07', 'AUGUST': '08',
            'SEPTEMBER': '09', 'OCTOBER': '10', 'NOVEMBER': '11', 'DECEMBER': '12'
        }

        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for idx, group in enumerate(payslip_groups, start=1):
                writer = PdfWriter()
                merged_text = ""

                for pg in group:
                    writer.add_page(reader.pages[pg])
                    merged_text += ocr_texts[pg] + "\n"

                # --- Extract info ---
                year_match = re.search(r'\b(20\d{2})\b', merged_text)
                month_match = re.search(
                    r'\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\b',
                    merged_text, re.IGNORECASE
                )

                ippis_match = (
                    re.search(r'IPPIS\s*Number[:\-]?\s*(\d{3,})', merged_text, re.IGNORECASE)
                    or re.search(r'(\d{3,})\s*Step', merged_text, re.IGNORECASE)
                    or re.search(r'FGN\s+CIVIL\s+SERVICE.*?(\d{5,6})', merged_text, re.IGNORECASE | re.DOTALL)
                    or re.search(r'FEDERAL\s+GOVERNMENT.*?(\d{5,6})', merged_text, re.IGNORECASE | re.DOTALL)
                    or re.search(r'\b(\d{6})\b', merged_text)
                )

                total_net_match = re.search(
                    r'Total\s+Net\s+Earnings[:\-]?\s*N?([\d,]+\.\d{2})',
                    merged_text, re.IGNORECASE
                )

                year = year_match.group(1) if year_match else "UnknownYear"
                month = month_map.get(month_match.group(1).upper(), "00") if month_match else "00"
                ippis = ippis_match.group(1) if ippis_match else f"UnknownIPPIS{idx}"

                filename = f"{year} {month} {ippis}.pdf"

                pdf_bytes_out = io.BytesIO()
                writer.write(pdf_bytes_out)
                pdf_bytes_out.seek(0)
                zipf.writestr(filename, pdf_bytes_out.read())

        zip_buffer.seek(0)

        # --- Download section ---
        st.success("🎉 All payslips split and processed successfully!")
        st.download_button(
            label="⬇️ Download All Payslips (ZIP)",
            data=zip_buffer,
            file_name="split_payslips.zip",
            mime="application/zip"
        )
else:
    st.info("Please upload your combined payslip PDF file above to begin.")
