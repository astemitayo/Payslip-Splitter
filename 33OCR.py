import os
import io
import re
from pdf2image import convert_from_path
from PyPDF2 import PdfReader, PdfWriter
import pytesseract

# --- Configuration ---
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
poppler_path = r"C:\Users\HP\Desktop\HDD\ARMTI\Payslip\poppler-25.07.0\Library\bin"

input_pdf = "OCT 2025.pdf"  # your combined payslip file
output_folder = "split_payslips"

os.makedirs(output_folder, exist_ok=True)

# --- Step 1: Convert all pages to images and OCR them ---
print("🔍 Performing OCR on PDF pages (memory-safe mode)...")
reader = PdfReader(io.BytesIO(input_pdf.read()))

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

    # --- Log results ---
    total_net = total_net_match.group(1) if total_net_match else "N/A"
    print(f"✅ Saved: {filename} ({len(group)} pages) | IPPIS: {ippis} | Total Net: ₦{total_net}")

print("\n🎉 All payslips have been split and renamed successfully!")
print(f"📁 Output folder: {os.path.abspath(output_folder)}")
