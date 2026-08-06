"""
PDF Generation and Storage Service
Compiles text lease agreements into PDF documents and uploads them to Supabase Storage.
"""

import io
from logging import getLogger
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from app.core.database import supabase_client

logger = getLogger("uvicorn")


async def generate_and_upload_lease_pdf(
    lease_id: str,
    contract_text: str,
    bucket_name: str = "lease-documents"
) -> str:
    """Compiles contract_text into a formatted PDF and uploads it to Supabase Storage.
    
    Returns the relative storage path in the bucket.
    """
    try:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=40,
            leftMargin=40,
            topMargin=40,
            bottomMargin=40
        )

        styles = getSampleStyleSheet()
        normal_style = ParagraphStyle(
            'ContractBody',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#1A1A1A")
        )

        story = []
        for line in contract_text.split("\n"):
            clean_line = line.strip()
            if not clean_line:
                story.append(Spacer(1, 8))
            elif clean_line.startswith("================="):
                story.append(Spacer(1, 10))
            else:
                formatted_line = clean_line.replace(" ", "&nbsp;")
                story.append(Paragraph(formatted_line, normal_style))
                story.append(Spacer(1, 2))

        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        # Supabase Storage path
        file_path = f"leases/{lease_id}/executed_lease.pdf"

        # Upload byte stream directly to Supabase Storage bucket
        supabase_client.storage.from_(bucket_name).upload(
            file_path,
            pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"}
        )

        logger.info(f"[PDF STORED] Created and uploaded PDF for lease {lease_id} to Supabase Storage: {file_path}")
        return file_path

    except Exception as e:
        logger.error(f"[PDF GENERATION ERROR] Failed for lease {lease_id}: {str(e)}")
        raise e