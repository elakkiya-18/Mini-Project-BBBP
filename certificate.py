
from pathlib import Path
from reportlab.lib.pagesizes import landscape, A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

BASE_DIR = Path(__file__).resolve().parent
CERT_DIR = BASE_DIR / "certificates"
CERT_DIR.mkdir(exist_ok=True)


def create_certificate(username, score, percentage, certificate_number, issued_date):
    safe_name = "".join(c for c in username if c.isalnum() or c in ("_", "-"))
    output = CERT_DIR / f"{safe_name}_certificate.pdf"

    width, height = landscape(A4)
    c = canvas.Canvas(str(output), pagesize=(width, height))

    # Border
    c.setLineWidth(4)
    c.rect(12*mm, 12*mm, width-24*mm, height-24*mm)

    c.setLineWidth(1)
    c.rect(18*mm, 18*mm, width-36*mm, height-36*mm)

    c.setTitle("Respect Quest Certificate")

    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(width/2, height-48*mm, "RESPECT QUEST")

    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width/2, height-65*mm, "CERTIFICATE OF COMPLETION")

    c.setFont("Helvetica", 14)
    c.drawCentredString(width/2, height-82*mm,
                        "This certificate is proudly presented to")

    c.setFont("Helvetica-Bold", 27)
    c.drawCentredString(width/2, height-100*mm, username)

    c.setFont("Helvetica", 14)
    c.drawCentredString(
        width/2, height-118*mm,
        "for successfully completing all 10 levels of the Respect Quest educational game."
    )

    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(width/2, height-140*mm,
                        f"Final Score: {score} / 1000    |    Percentage: {percentage}%")

    c.setFont("Helvetica", 11)
    c.drawString(28*mm, 25*mm, f"Certificate No: {certificate_number}")
    c.drawRightString(width-28*mm, 25*mm, f"Issued: {issued_date}")

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width/2, 38*mm,
                        "RESPECT  •  EQUALITY  •  DIGNITY")

    c.save()
    return str(output)
