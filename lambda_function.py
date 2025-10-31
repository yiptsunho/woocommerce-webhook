import os
import json
import hmac
import hashlib
import base64
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime

# --- NEW: Use cryptography ---
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

import qrcode
from io import BytesIO

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
AES_KEY = os.environ["AES_KEY"]

# -------------------------------------------------
# Logger
# -------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# -------------------------------------------------
# HTML Template
# -------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Booking Confirmed</title></head>
<body style="font-family:Arial; color:#333;">
  <h2>Hello {first_name} {last_name},</h2>
  <p>Thank you for order <strong>#{order_number}</strong> on {date_created}.</p>
  <h3>Booking Time</h3>
  <p><strong>Start:</strong> {start_time}</p>
  <p><strong>End:</strong> {end_time}</p>
  <h3>Your QR Code</h3>
  <p>Scan at venue:</p>
  <img src="cid:qr_code.png" style="width:200px; height:200px;">
  <h3>Order Summary</h3>
  <table border="0" cellpadding="6" style="border-collapse:collapse; width:100%;">
    <thead><tr style="background:#f9f9f9;"><th align="left">Item</th><th align="right">Qty</th><th align="right">Price</th></tr></thead>
    <tbody>{line_items}</tbody>
    <tfoot><tr><td colspan="2" align="right"><strong>Total:</strong></td><td align="right"><strong>{total} {currency}</strong></td></tr></tfoot>
  </table>
  <p>We’ll see you soon!</p>
</body></html>
""".strip()

# -------------------------------------------------
def pkcs7_pad(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    return padder.update(data) + padder.finalize()

# -------------------------------------------------
def encrypt_aes_ecb(plaintext: str) -> str:
    key = AES_KEY.encode('utf-8')
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    padded = pkcs7_pad(plaintext.encode('utf-8'))
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode('utf-8')

# -------------------------------------------------
def generate_qr_png(data: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()

# -------------------------------------------------
def extract_meta_value(meta_data, key):
    for meta in meta_data:
        if meta.get("key") == key:
            value = meta.get("value", [])
            return value[0] if isinstance(value, list) and value else value
    return None

# -------------------------------------------------
def format_datetime(dt_str: str) -> str:
    try:
        dt = datetime.strptime(dt_str.strip(), "%d/%m/%Y %H:%M")
        return dt.strftime("%Y%m%d%H%M%S")
    except:
        return "19700101000000"

# -------------------------------------------------
def send_email(to_email: str, html: str, qr_png: bytes):
    msg = MIMEMultipart("related")
    msg["From"] = GMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = "Your Booking QR Code"

    msg.attach(MIMEText(html, "html"))

    qr_image = MIMEImage(qr_png)
    qr_image.add_header("Content-ID", "<qr_code.png>")
    msg.attach(qr_image)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER.strip(), GMAIL_APP_PASSWORD.strip())
        server.send_message(msg)
    logger.info("Email sent", extra={"to": to_email})

# -------------------------------------------------
def lambda_handler(event, context):
    logger.info("=== LAMBDA START ===")

    try:
        raw_body = event.get("body", "")
        if event.get("isBase64Encoded", False):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        payload = json.loads(raw_body)

        # Extract times
        start_raw = None
        end_raw = None
        for item in payload.get("line_items", []):
            meta = item.get("meta_data", [])
            start_raw = extract_meta_value(meta, "phive_display_time_from") or start_raw
            end_raw = extract_meta_value(meta, "phive_display_time_to") or end_raw
            if start_raw and end_raw:
                break

        if not start_raw or not end_raw:
            start_raw = end_raw = "01/01/2025 00:00"

        start_dt = format_datetime(start_raw)
        end_dt = format_datetime(end_raw)

        qr_data = f"[,,{start_dt},{end_dt},,,,,]"
        encrypted_b64 = encrypt_aes_ecb(qr_data)
        final_qr_string = "SK01" + encrypted_b64

        qr_png = generate_qr_png(final_qr_string)

        billing = payload.get("billing", {})
        customer_email = billing.get("email")
        if not customer_email:
            return {"statusCode": 400, "body": "No email"}

        items_html = ""
        for item in payload.get("line_items", []):
            items_html += f"<tr><td>{item.get('name','')}</td><td align='right'>{item.get('quantity',0)}</td><td align='right'>{item.get('total','')} {payload.get('currency','')}</td></tr>"

        html_body = HTML_TEMPLATE.format(
            first_name=billing.get("first_name", ""),
            last_name=billing.get("last_name", ""),
            order_number=payload.get("number", "N/A"),
            date_created=payload.get("date_created", "")[:19].replace("T", " "),
            start_time=start_raw,
            end_time=end_raw,
            total=payload.get("total", "0"),
            currency=payload.get("currency", ""),
            line_items=items_html
        )

        send_email(customer_email, html_body, qr_png)

        logger.info("=== SUCCESS ===")
        return {"statusCode": 200, "body": "OK"}

    except Exception as e:
        logger.exception("ERROR")
        # TODO: temporarily solve woocommerce disabling the webhook
        return {"statusCode": 200, "body": "Error"}