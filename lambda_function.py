import os
import json
import base64
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

# Cryptography
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
AES_KEY = os.environ["AES_KEY"]
GMAIL_USER_DISPLAY_NAME = os.environ["GMAIL_USER_DISPLAY_NAME"]
AREA_ID = "11001"
QR_CODE_TYPE = "6"
IS_ENCRYPT_QR_CODE = os.environ["IS_ENCRYPT_QR_CODE"]

# Global SMTP connection (reused across invocations)
SMTP_CONNECTION = None

# -------------------------------------------------
# Logger
# -------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# -------------------------------------------------
# Load HTML Template
# -------------------------------------------------
def load_template():
    with open("template.html", "r", encoding="utf-8") as f:
        return f.read()

def load_binary(path: str):
    with open(path, "rb") as f:
        return f.read()

HTML_TEMPLATE = load_template()
CLIENT_LOGO = load_binary("logo.png")          # ← your client's logo file
CLIENT_MAP = load_binary("map.png")            # ← your client's venue map file

# -------------------------------------------------
# Helper: PKCS7 + AES-ECB
# -------------------------------------------------
def pkcs7_pad(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    return padder.update(data) + padder.finalize()

def encrypt_aes_ecb(plaintext: str) -> str:
    key = AES_KEY.encode('utf-8')[:32]
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    padded = pkcs7_pad(plaintext.encode('utf-8'))
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode('utf-8')

# -------------------------------------------------
# QR Code
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
# Extract Meta
# -------------------------------------------------
def extract_meta_value(meta_data, key):
    for meta in meta_data:
        if meta.get("key") == key:
            value = meta.get("value", [])
            return value[0] if isinstance(value, list) and value else value
    return None

# -------------------------------------------------
# Format Address
# -------------------------------------------------
def format_address(addr):
    if not addr: return "N/A"
    parts = [addr.get("first_name", ""), addr.get("last_name", "")]
    parts += [addr.get("address_1", ""), addr.get("address_2", "")]
    parts += [addr.get("city", ""), addr.get("state", ""), addr.get("postcode", "")]
    parts += [addr.get("country", "")]
    return ", ".join(filter(None, parts))

# -------------------------------------------------
# Reuse SMTP Connection
# -------------------------------------------------
def get_smtp_connection():
    global SMTP_CONNECTION
    if SMTP_CONNECTION is None:
        SMTP_CONNECTION = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
        SMTP_CONNECTION.login(GMAIL_USER.strip(), GMAIL_APP_PASSWORD.strip())
    return SMTP_CONNECTION

# -------------------------------------------------
# Send Email
# -------------------------------------------------
def send_email(to_email: str, html: str, qr_png: bytes, order_number: str):
    msg = MIMEMultipart("related")
    msg["From"] = f"{GMAIL_USER_DISPLAY_NAME} <{GMAIL_USER}>"
    msg["To"] = to_email
    msg["Subject"] = f"預訂成功！您的入場二維碼 - 訂單 #{order_number}"

    msg.attach(MIMEText(html, "html"))

    qr_image = MIMEImage(qr_png)
    qr_image.add_header("Content-ID", "<qr_code.png>")
    msg.attach(qr_image)

    # Client Logo
    logo_img = MIMEImage(CLIENT_LOGO)
    logo_img.add_header("Content-ID", "<logo>")
    msg.attach(logo_img)

    # Client Map
    map_img = MIMEImage(CLIENT_MAP)
    map_img.add_header("Content-ID", "<map>")
    msg.attach(map_img)

    server = get_smtp_connection()
    server.send_message(msg)
    logger.info("Email sent", extra={"to": to_email})

# -------------------------------------------------
# Main Handler
# -------------------------------------------------
def lambda_handler(event, context):
    logger.info("Lambda invoked")

    try:
        # Parse body
        raw_body = event.get("body", "")
        if event.get("isBase64Encoded", False):
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        payload = json.loads(raw_body)

        pretty_json = json.dumps(raw_body, indent=4, sort_keys=True)
        print(pretty_json)

        # Extract times
        start_raw = end_raw = None
        for item in payload.get("line_items", []):
            meta = item.get("meta_data", [])
            start_raw = extract_meta_value(meta, "phive_display_time_from") or start_raw
            end_raw = extract_meta_value(meta, "phive_display_time_to") or end_raw
            if start_raw and end_raw:
                break

        if not start_raw or not end_raw:
            start_raw = end_raw = "01/01/2025 00:00"

        # Parse and adjust start time (-10 mins)
        dt_start = datetime.strptime(start_raw.strip(), "%d/%m/%Y %H:%M")
        dt_end = datetime.strptime(end_raw.strip(), "%d/%m/%Y %H:%M")
        entry_time = (dt_start - timedelta(minutes=10)).strftime("%d/%m/%Y %H:%M")

        start_dt = dt_start.strftime("%Y%m%d%H%M%S")
        end_dt = dt_end.strftime("%Y%m%d%H%M%S")

        # QR data
        qr_data = f"[,{AREA_ID},{start_dt},{end_dt},,,{QR_CODE_TYPE},]"
        if IS_ENCRYPT_QR_CODE:
            final_qr_string = "SK01" + encrypt_aes_ecb(qr_data)
        else:
            final_qr_string = "SK01" + qr_data

        qr_png = generate_qr_png(final_qr_string)

        # Customer
        billing = payload.get("billing", {})
        shipping = payload.get("shipping", {})
        customer_email = billing.get("email")
        if not customer_email:
            return {"statusCode": 400, "body": "No email"}

        # Line items
        items_html = ""
        for item in payload.get("line_items", []):
            items_html += f"<tr><td>{item.get('name','')}</td><td>{item.get('quantity',0)}</td><td>{item.get('total','')} {payload.get('currency','')}</td></tr>"

        order_number = payload.get("number", "N/A")
        # Render template
        html_body = HTML_TEMPLATE.format(
            first_name=billing.get("first_name", ""),
            last_name=billing.get("last_name", ""),
            order_number=order_number,
            date_created=payload.get("date_created", "")[:19].replace("T", " "),
            entry_time=entry_time,
            start_time=start_raw,
            end_time=end_raw,
            total=payload.get("total", "0"),
            currency=payload.get("currency", ""),
            line_items=items_html,
            billing_address=format_address(billing),
            shipping_address=format_address(shipping),
            payment_method=payload.get("payment_method_title", "N/A"),
            year=datetime.now().year
        )

        send_email(customer_email, html_body, qr_png, order_number)

        logger.info("Success")
        return {"statusCode": 200, "body": "OK"}

    except Exception as e:
        logger.exception("Error")
        return {"statusCode": 200, "body": "OK"}  # Keep webhook alive