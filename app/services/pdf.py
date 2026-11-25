"""PDF rendering of an invoice with ReportLab."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import get_settings
from app.models import Invoice, Party
from app.services.gstin import STATE_CODES
from app.services.money import format_inr, format_rate


def render_invoice_pdf(invoice: Invoice, party: Party) -> bytes:
    s = get_settings()
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=12)
    right = ParagraphStyle("right", parent=small, alignment=2)
    intra = invoice.supply_type == "intra"
    is_sale = invoice.invoice_type == "sale"

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=invoice.invoice_no, author=s.company_name)
    story = [Paragraph("TAX INVOICE" if is_sale else "PURCHASE INVOICE", styles["Title"])]
    if invoice.status == "cancelled":
        story.append(Paragraph('<font color="red"><b>CANCELLED</b></font>', ParagraphStyle("c", parent=styles["Title"], fontSize=16)))

    state = lambda code: f"{STATE_CODES.get(code, '?')} ({code})"  # noqa: E731
    us = f"<b>{s.company_name}</b><br/>GSTIN: {s.company_gstin}<br/>{s.company_address}<br/>State: {state(s.company_state_code)}"
    them = (f"<b>{party.name}</b> ({'Customer' if party.party_type == 'customer' else 'Supplier'})<br/>"
            f"GSTIN: {party.gstin or 'Unregistered'}<br/>{party.address or ''}<br/>Place of supply: {state(invoice.place_of_supply)}")
    meta = f"<b>Invoice No:</b> {invoice.invoice_no}<br/><b>Date:</b> {invoice.invoice_date:%d-%m-%Y}<br/><b>Supply:</b> {'Intra-state (CGST+SGST)' if intra else 'Inter-state (IGST)'}"
    head = Table([[Paragraph(us, small), Paragraph(them, small), Paragraph(meta, small)]], colWidths=[62 * mm, 70 * mm, 54 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                              ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    story += [head, Spacer(1, 6 * mm)]

    header = ["#", "Description", "HSN", "Qty", "Rate", "Taxable", "GST"] + (["CGST", "SGST"] if intra else ["IGST"]) + ["Total"]
    rows = [header]
    for ln in invoice.lines:
        tax_cols = [format_inr(ln.cgst), format_inr(ln.sgst)] if intra else [format_inr(ln.igst)]
        rows.append([ln.position, Paragraph(ln.description, small), ln.hsn, ln.quantity, format_inr(ln.unit_price),
                     format_inr(ln.taxable_value), format_rate(ln.gst_rate_bp), *tax_cols, format_inr(ln.line_total)])
    n_tax = 2 if intra else 1
    widths = [8, 48, 16, 11, 20, 22, 11] + [18] * n_tax + [24]
    scale = 186 / sum(widths)
    table = Table(rows, colWidths=[w * scale * mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [table, Spacer(1, 5 * mm)]

    totals = [["Taxable value", format_inr(invoice.taxable_total)]]
    totals += [["CGST", format_inr(invoice.cgst_total)], ["SGST", format_inr(invoice.sgst_total)]] if intra else [["IGST", format_inr(invoice.igst_total)]]
    totals += [["Grand total (INR)", format_inr(invoice.grand_total)]]
    t = Table(totals, colWidths=[40 * mm, 32 * mm], hAlign="RIGHT")
    t.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                           ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story += [t, Spacer(1, 8 * mm), Paragraph("This is a computer-generated invoice. Amounts are in Indian Rupees.", right)]
    doc.build(story)
    return buf.getvalue()
