import json
import os
import time
import traceback
from abc import ABC, abstractmethod
from io import BytesIO
from PIL import Image
from google import genai
from django.conf import settings
import dotenv

dotenv.load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)  # ✅ Keyword argument


from google.genai import types

# ------------------------------------------------------------------
# Abstract Base Class for Extractors
# ------------------------------------------------------------------
class InvoiceExtractor(ABC):
    @abstractmethod
    def extract_from_file(self, file_content: bytes, mime_type: str) -> dict:
        pass

# ------------------------------------------------------------------
# Gemini Implementation (Official SDK)
# ------------------------------------------------------------------
class GeminiInvoiceExtractor(InvoiceExtractor):
    """Uses Google Gemini via the official google-genai SDK."""
    
    SYSTEM_PROMPT = """You are a senior invoice extraction AI specializing in Balkan and International invoices. 
    Extract ALL information from the provided invoice image or PDF into a STRICT JSON format.
    
    REQUIRED JSON STRUCTURE (MATCH THESE KEYS EXACTLY):
    {
        "vendor": {
            "name": "Full legal name",
            "address": "Full address",
            "vat_id": "VAT/Tax ID (or JIB/OIB)",
            "jib_oib": "Same as vat_id",
            "bank_name": "Bank name (Look at bottom of page if not in header)",
            "bank_account": "IBAN or Account number (Look at bottom of page)",
            "swift_bic": "BIC/SWIFT code (Look at bottom of page)",
            "phone": "string",
            "email": "string"
        },
        "customer": {
            "name": "Full name",
            "address": "Full address",
            "vat_id": "VAT/Tax ID (or JIB/OIB)",
            "jib_oib": "Same as vat_id"
        },
        "invoice": {
            "number": "Invoice number",
            "date": "YYYY-MM-DD",
            "due_date": "YYYY-MM-DD",
            "reference_number": "Reference number (Poziv na broj)",
            "currency": "3-letter code (e.g. EUR, USD, UAH, KM)",
            "payment_status": "Paid or Not Paid",
            "jir": "JIR code if present",
            "zki": "ZKI code if present"
        },
        "financials": {
            "subtotal": 0.00,
            "tax_total": 0.00,
            "amount_due": 0.00
        },
        "line_items": [
            {
                "description": "Item description",
                "quantity": 1,
                "unit_price": 0.00,
                "total": 0.00
            }
        ],
        "language": "Detected language"
    }

    RULES:
    1. If a value is missing, use null.
    2. Ensure dates are in YYYY-MM-DD format.
    3. Ensure numbers are floats (e.g. 1200.00).
    4. "Kolicina" maps to "quantity". "Cijena" maps to "unit_price".
    5. Look for JIR/ZKI codes at the bottom.
    6. Look for Bank/IBAN/SWIFT at the absolute bottom of the invoice.
    """
    
    def __init__(self):
        self.client = client
    
    def _prepare_image(self, image_content: bytes) -> bytes:
        pil_img = Image.open(BytesIO(image_content))
        if pil_img.mode == "RGBA": pil_img = pil_img.convert("RGB")
        if pil_img.width > 1024:
            ratio = 1024 / pil_img.width
            pil_img = pil_img.resize((1024, int(pil_img.height * ratio)), Image.LANCZOS)
        buffer = BytesIO()
        pil_img.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    
    def extract_from_file(self, file_content: bytes, mime_type: str) -> dict:
        if not self.client: raise ValueError("Client not initialized")
        
        print(f"\n[STEP 1] Preparing data for extraction (MIME: {mime_type})...")
        data = self._prepare_image(file_content) if mime_type.startswith("image/") else file_content
        parts = [
            self.SYSTEM_PROMPT,
            types.Part.from_bytes(data=data, mime_type=mime_type)
        ]
        
        # Use full model names as listed in your environment
        models = [
            "models/gemini-2.5-flash-lite", 
            "models/gemini-2.5-flash", 
            "models/gemini-2.0-flash", 
            "models/gemini-2.0-flash-lite"
        ]
        for model in models:
            # Universal exponential backoff for all models
            retries = 5 # Increased to 5 retries to reach 16s backoff
            delay = 1
            
            for attempt in range(retries + 1):
                try:
                    print(f"[STEP 2] Attempting extraction with model: {model} (Attempt {attempt+1})...")
                    response = self.client.models.generate_content(
                        model=model, 
                        contents=parts, 
                        config={'response_mime_type': 'application/json'}
                    )
                    if response and response.text: 
                        print(f"[SUCCESS] Data extracted successfully using {model}.")
                        print(f"[RAW RESPONSE] {response.text[:500]}..." if len(response.text) > 500 else f"[RAW RESPONSE] {response.text}")
                        return json.loads(response.text)
                except Exception as e:
                    error_msg = str(e).upper()
                    is_quota = any(x in error_msg for x in ["429", "RESOURCE_EXHAUSTED", "QUOTA", "LIMIT"])
                    
                    if is_quota and attempt < retries:
                        print(f"[BACKOFF] {model} hit quota. Retrying in {delay}s... (Attempt {attempt+1}/{retries+1})")
                        time.sleep(delay)
                        delay *= 2
                        if delay > 16: delay = 16 # Increased to 16 seconds
                        continue
                    else:
                        print(f"[RETRY] Model {model} failed or exhausted: {str(e)[:200]}")
                        break # Move to next model
        
        raise ValueError("All 2.x models failed (likely quota exhausted)")

    def transform_invoice(self, original_data: dict, target_country: str, target_lang: str = "English") -> dict:
        if not self.client:
            print("CRITICAL: Client is NULL in transform_invoice")
            return original_data

        print(f"\n[TRANSFORM] Starting International Transformation for: {target_country} ({target_lang})")
        prompt = f"""
        # TASK: RESEARCH AND TRANSFORM INVOICE FOR {target_country} (2025).
        
        1. RESEARCH: Use your internal knowledge and search tools to identify the LATEST (2025) legal, fiscal, and tax requirements for an invoice in {target_country}.
        2. ANALYZE: Compare the {target_country} requirements against the provided data.
        3. TRANSFORM: Generate a compliant invoice. 
        
        ## MISSING INFORMATION PROTOCOL:
        - NEVER mark a field as "REQUIRED_FIELD" if the information exists in the original data. If the data is present (even in another language), TRANSLATE or PRESERVE it.
        - Only use "REQUIRED_FIELD" if the information is absolutely nowhere to be found in the original document.
        - Ensure all financial totals (subtotal, tax_total, amount_due) are calculated correctly and included. NEVER return 0 if there are line items.
        - List any truly missing field names in a `missing_fields` array.

        ## COUNTRY-SPECIFIC REFINEMENTS:
        If target_country is GERMANY:
        - Use professional terminology: "Rechnung" (Invoice), "USt-IdNr." (VAT ID), "Leistungsdatum" (Performance Date).
        - Line Items MUST include: "quantity" (e.g., 4 Std, 1 x pauschal), "unit_price" (Einzelpreis), and "description" (Leistung).
        - Financials MUST show: "Netto" (subtotal), "Umsatzsteuer" (tax_total), and "Brutto" (amount_due).
        - Ensure the IBAN and BIC/SWIFT are formatted correctly in the vendor section.
        - Add a polite German closing: "Mit freundlichen Grüßen".

        ## DATA TO TRANSFORM:
        {json.dumps(original_data, indent=2)}

        ## RETURN FORMAT (JSON ONLY):
        {{
            "transformed_data": {{ 
                "vendor": {{
                    "name": "string", "address": "string", "vat_id": "string", "jib_oib": "string",
                    "bank_name": "string", "bank_account": "string", "swift_bic": "string"
                }},
                "customer": {{
                    "name": "string", "address": "string", "vat_id": "string", "jib_oib": "string"
                }},
                "invoice": {{
                    "number": "string", "date": "YYYY-MM-DD", "due_date": "YYYY-MM-DD", 
                    "reference_number": "string", "currency": "string", "jir": "string", "zki": "string"
                }},
                "financials": {{
                    "subtotal": 0.00, "tax_total": 0.00, "tax_rate": "19%", "amount_due": 0.00
                }},
                "line_items": [
                    {{ "description": "string", "quantity": 0, "unit_price": 0.00, "total": 0.00 }}
                ]
            }},
            "missing_fields": ["Field 1 Name", "Field 2 Name"],
            "legal_research_summary": "Short summary of the specific rules applied for {{target_country}}"
        }}
        """
        
        contents = [prompt]
        # Use full model names as listed in your environment and apply backoff to ALL of them
        models = [
            "models/gemini-2.5-flash-lite", 
            "models/gemini-2.5-flash", 
            "models/gemini-2.0-flash", 
            "models/gemini-2.0-flash-lite"
        ]
        
        for model_name in models:
            retries = 5 # Increased to reach 16s backoff
            delay = 1

            for attempt in range(retries + 1):
                try:
                    print(f"[RESEARCH] Model {model_name} (Attempt {attempt+1}) is researching 2025 {target_country} rules...")
                    # REMOVED response_mime_type to allow Tool Use (Google Search)
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config={
                            'tools': [{'google_search': {}}] 
                        }
                    )
                    
                    if response and response.text:
                        print(f"[SUCCESS] Transformation complete using {model_name}.")
                        
                        # Use regex to extract the first valid JSON object/array from the text
                        import re
                        raw_text = response.text
                        json_match = re.search(r'(\{.*\}|\[.*\])', raw_text, re.DOTALL)
                        
                        if json_match:
                            clean_text = json_match.group(0)
                            print(f"[EXTRACTED JSON] {clean_text[:300]}...")
                            return json.loads(clean_text)
                        else:
                            # Fallback to previous cleaning logic if regex fails
                            clean_text = raw_text.replace('```json', '').replace('```', '').strip()
                            return json.loads(clean_text)
                except Exception as e:
                    error_msg = str(e).upper()
                    is_quota = any(x in error_msg for x in ["429", "RESOURCE_EXHAUSTED", "QUOTA", "LIMIT"])
                    
                    if is_quota and attempt < retries:
                        print(f"[BACKOFF] {model_name} hit quota. Retrying in {delay}s... (Attempt {attempt+1}/{retries+1})")
                        time.sleep(delay)
                        delay *= 2
                        if delay > 16: delay = 16 # Increased to 16 seconds
                        continue
                    else:
                        print(f"[RETRY] Research failed with {model_name}: {str(e)[:200]}")
                        break
        
        print("[WARNING] All transformation models failed. Falling back to original data.")
        return {"transformed_data": original_data, "missing_fields": [], "legal_research_summary": "Using fallback logic."}

def get_invoice_extractor() -> InvoiceExtractor:
    return GeminiInvoiceExtractor()

# ------------------------------------------------------------------
# Advanced Export Utilities (PDF, XML, CSV)
# ------------------------------------------------------------------
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

def normalize_text(text: str) -> str:
    """Replace special characters with ASCII equivalents to prevent black squares in PDF."""
    if not text: return ""
    replacements = {
        'ć': 'c', 'Ć': 'C',
        'č': 'c', 'Č': 'C',
        'ž': 'z', 'Ž': 'Z',
        'š': 's', 'Š': 'S',
        'đ': 'd', 'Đ': 'D'
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text

def generate_invoice_pdf(data: dict) -> BytesIO:
    """Generate a clean PDF from invoice dictionary with localization and char support."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []
    
    # Detect language for labels
    lang = data.get('language', 'en').lower()
    if lang == 'german' or lang == 'de':
        labels = {
            'title': 'RECHNUNG', 'from': 'VON', 'to': 'AN',
            'inv_no': 'Rechnungsnummer', 'date': 'Datum', 'due_date': 'Falligkeitsdatum',
            'currency': 'Wahrung', 'desc': 'Beschreibung', 'qty': 'Menge',
            'unit_p': 'Einzelpreis', 'total': 'Gesamt', 'subtotal': 'Zwischensumme',
            'tax': 'Steuer', 'total_due': 'Gesamtbetrag'
        }
    elif lang == 'bosnian' or lang == 'bs':
        labels = {
            'title': 'FAKTURA', 'from': 'POSILJALAC', 'to': 'PRIMALAC',
            'inv_no': 'Broj racuna', 'date': 'Datum', 'due_date': 'Rok placanja',
            'currency': 'Valuta', 'desc': 'Opis', 'qty': 'Kol',
            'unit_p': 'Cijena', 'total': 'Ukupno', 'subtotal': 'Osnovica',
            'tax': 'Porez', 'total_due': 'Ukupno za platiti'
        }
    else:
        labels = {
            'title': 'INVOICE', 'from': 'FROM', 'to': 'TO',
            'inv_no': 'Invoice Number', 'date': 'Date', 'due_date': 'Due Date',
            'currency': 'Currency', 'desc': 'Description', 'qty': 'Qty',
            'unit_p': 'Unit Price', 'total': 'Total', 'subtotal': 'Subtotal',
            'tax': 'Tax Total', 'total_due': 'Total Due'
        }

    # Title
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], spaceAfter=20, textColor=colors.HexColor("#6366f1"))
    elements.append(Paragraph(labels['title'], title_style))
    
    # Header Info (Vendor & Customer)
    vendor = data.get('vendor', {})
    customer = data.get('customer', {})
    inv = data.get('invoice', {})
    
    header_data = [
        [Paragraph(f"<b>{labels['from']}:</b><br/>{normalize_text(vendor.get('name', 'N/A'))}<br/>{normalize_text(vendor.get('address', ''))}<br/>{labels.get('vat_tax', 'VAT/Tax ID')}: {normalize_text(vendor.get('vat_id') or vendor.get('tax_id', ''))}<br/>{normalize_text(vendor.get('email', ''))}", styles['Normal']),
         Paragraph(f"<b>{labels['to']}:</b><br/>{normalize_text(customer.get('name', 'N/A'))}<br/>{normalize_text(customer.get('address', ''))}<br/>{labels.get('vat_tax', 'VAT/Tax ID')}: {normalize_text(customer.get('vat_id', ''))}", styles['Normal'])]
    ]
    t_header = Table(header_data, colWidths=[250, 250])
    t_header.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    elements.append(t_header)
    elements.append(Spacer(1, 20))
    
    # Invoice Details (Number, Date, Performance Date)
    perf_label = "Performance Date" if lang not in ['de', 'german'] else "Leistungsdatum"
    if lang in ['bs', 'bosnian']: perf_label = "Datum isporuke"

    details = [
        [f"{labels['inv_no']}: {normalize_text(inv.get('number', 'N/A'))}", f"{labels['date']}: {normalize_text(inv.get('date', 'N/A'))}"],
        [f"{labels['due_date']}: {normalize_text(inv.get('due_date', 'N/A'))}", f"{perf_label}: {normalize_text(inv.get('performance_date', inv.get('date', 'N/A')))}"],
        [f"{labels['currency']}: {normalize_text(inv.get('currency', 'N/A'))}", ""]
    ]
    t_details = Table(details, colWidths=[250, 250])
    elements.append(t_details)
    elements.append(Spacer(1, 20))
    
    # Items Table
    items_data = [[labels['desc'], labels['qty'], header_price, header_total]]
    for item in data.get('line_items', []):
        items_data.append([
            Paragraph(normalize_text(str(item.get('description', ''))), styles['Normal']),
            str(item.get('quantity', 1)),
            str(item.get('unit_price', 0)),
            str(item.get('total', 0))
        ])
    
    t_items = Table(items_data, colWidths=[300, 40, 80, 80])
    t_items.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f8fafc")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor("#1e293b")),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#e2e8f0"))
    ]))
    elements.append(t_items)
    elements.append(Spacer(1, 20))
    
    # Financials (Correct Alignment with Key Fallbacks)
    f = data.get('financials', {})
    subtotal = f.get('subtotal') or f.get('net_amount') or 0
    tax = f.get('tax_total') or f.get('tax') or f.get('vat_amount') or 0
    total = f.get('amount_due') or f.get('total') or f.get('gross_amount') or 0
    tax_rate = f.get('tax_rate') or f.get('vat_rate') or ""
    
    # Dynamic Tax Label (e.g. 19% Umsatzsteuer)
    tax_label = labels['tax']
    if tax_rate:
        tax_label = f"{tax_rate}% {tax_label}" if "%" not in str(tax_rate) else f"{tax_rate} {tax_label}"

    # Update headers to include currency in parentheses
    curr = inv.get('currency', '')
    header_price = f"{labels['unit_p']} ({curr})" if curr else labels['unit_p']
    header_total = f"{labels['total']} ({curr})" if curr else labels['total']
    
    fin_data = [
        ["", labels['subtotal'], f"{subtotal}"],
        ["", tax_label, f"{tax}"],
        ["", Paragraph(f"<b>{labels['total_due']}</b>", styles['Normal']), Paragraph(f"<b>{total}</b>", styles['Normal'])]
    ]
    t_fin = Table(fin_data, colWidths=[300, 120, 80])
    t_fin.setStyle(TableStyle([
        ('ALIGN', (1,0), (1,-1), 'LEFT'),
        ('ALIGN', (2,0), (2,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LINEABOVE', (1,2), (2,2), 1, colors.black),
    ]))
    elements.append(t_fin)
    elements.append(Spacer(1, 30))
    
    # Banking Info (Critical for International)
    v = data.get('vendor', {})
    if v.get('bank_name') or v.get('bank_account'):
        elements.append(Paragraph(f"<b>{labels.get('bank', 'Payment Information')}:</b>", styles['Heading3']))
        bank_info = f"""
        {normalize_text(v.get('bank_name', ''))}<br/>
        IBAN: {normalize_text(v.get('bank_account', ''))}<br/>
        SWIFT/BIC: {normalize_text(v.get('swift_bic', ''))}
        """
        elements.append(Paragraph(bank_info, styles['Normal']))
        elements.append(Spacer(1, 20))

    # Legal Research & Notes (Germany specific etc.)
    l_notes = data.get('legal_notes') or data.get('legal_research_summary')
    if l_notes:
        note_style = ParagraphStyle('NoteStyle', parent=styles['Normal'], fontSize=8, textColor=colors.darkgray, backColor=colors.whitesmoke, borderPadding=5)
        elements.append(Paragraph(f"<b>Legal Compliance Notes:</b><br/>{normalize_text(l_notes)}", note_style))
        elements.append(Spacer(1, 10))

    # Reverse Charge / Legal Notes
    if inv.get('reverse_charge_note'):
        elements.append(Paragraph(f"<b>Note:</b> {normalize_text(inv.get('reverse_charge_note'))}", styles['Normal']))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer

def dict_to_eu_einvoice_xml(invoice_data: dict) -> str:
    """Convert extracted dict to a basic UBL/XML structure."""
    line_items = invoice_data.get("line_items", [])
    lines_xml = ""
    for idx, item in enumerate(line_items):
        lines_xml += f"""
        <cac:InvoiceLine>
            <cbc:ID>{idx+1}</cbc:ID>
            <cbc:InvoicedQuantity unitCode="C62">{item.get('quantity', 0)}</cbc:InvoicedQuantity>
            <cac:Item><cbc:Name>{item.get('description', 'Item')}</cbc:Name></cac:Item>
            <cac:Price><cbc:PriceAmount currencyID="{invoice_data.get('invoice', {}).get('currency', 'EUR')}">{item.get('unit_price', 0)}</cbc:PriceAmount></cac:Price>
        </cac:InvoiceLine>"""
    
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" 
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" 
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
    <cbc:ID>{invoice_data.get('invoice', {}).get('number', '---')}</cbc:ID>
    <cbc:IssueDate>{invoice_data.get('invoice', {}).get('date', '2021-01-01')}</cbc:IssueDate>
    <cac:AccountingSupplierParty>
        <cac:Party><cac:PartyName><cbc:Name>{invoice_data.get('vendor', {}).get('name', 'Unknown')}</cbc:Name></cac:PartyName></cac:Party>
    </cac:AccountingSupplierParty>
    <cac:LegalMonetaryTotal>
        <cbc:PayableAmount currencyID="{invoice_data.get('invoice', {}).get('currency', 'EUR')}">{invoice_data.get('financials', {}).get('amount_due', 0)}</cbc:PayableAmount>
    </cac:LegalMonetaryTotal>
    {lines_xml}
</Invoice>"""

def dict_to_csv(invoice_data: dict) -> str:
    """Convert invoice data to a CSV string."""
    import csv
    from io import StringIO
    output = StringIO()
    writer = csv.writer(output)
    
    # Basic header
    writer.writerow(['Category', 'Field', 'Value'])
    
    v = invoice_data.get('vendor', {})
    for key, val in v.items(): writer.writerow(['Vendor', key, val])
    
    i = invoice_data.get('invoice', {})
    for key, val in i.items(): writer.writerow(['Invoice', key, val])
    
    f = invoice_data.get('financials', {})
    for key, val in f.items(): writer.writerow(['Financials', key, val])
    
    writer.writerow([])
    writer.writerow(['Line Items', 'Description', 'Qty', 'Price', 'Total'])
    for item in invoice_data.get('line_items', []):
        writer.writerow(['Item', item.get('description'), item.get('quantity'), item.get('unit_price'), item.get('total')])
        
    return output.getvalue()