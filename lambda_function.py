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
IS_ENCRYPT_QR_CODE = os.environ.get("IS_ENCRYPT_QR_CODE", "true").lower() == "true"

SMTP_CONNECTION = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# -------------------------------------------------
# Load static files once
# -------------------------------------------------
def load_binary(path: str):
    with open(path, "rb") as f:
        return f.read()

HTML_TEMPLATE = open("template.html", "r", encoding="utf-8").read()
CLIENT_LOGO   = load_binary("logo_black.png")
CLIENT_MAP    = load_binary("map.png")
IG_ICON       = load_binary("ig.png")
FB_ICON       = load_binary("fb.png")
WA_ICON       = load_binary("wa.png")

# -------------------------------------------------
# Encryption & QR
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

def generate_qr_png(data: str) -> bytes:
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def extract_meta_value(meta_data, key):
    for meta in meta_data:
        if meta.get("key") == key:
            value = meta.get("value")
            if isinstance(value, list) and value:
                return value[0]
            return value
    return None

def format_address(addr):
    if not addr: return "N/A"
    parts = [addr.get("first_name", ""), addr.get("last_name", "")]
    parts += [addr.get("address_1", ""), addr.get("address_2", "")]
    parts += [addr.get("city", ""), addr.get("state", ""), addr.get("postcode", "")]
    parts += [addr.get("country", "")]
    return ", ".join(filter(None, parts))

def get_smtp_connection():
    global SMTP_CONNECTION
    if SMTP_CONNECTION is None:
        SMTP_CONNECTION = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
        SMTP_CONNECTION.login(GMAIL_USER.strip(), GMAIL_APP_PASSWORD.strip())
    return SMTP_CONNECTION

# -------------------------------------------------
# Send Email – now accepts list of timeslots
# -------------------------------------------------
def send_email(to_email: str, html: str, timeslots: list, order_number: str):
    msg = MIMEMultipart("related")
    msg["From"] = f"{GMAIL_USER_DISPLAY_NAME} <{GMAIL_USER}>"
    msg["To"] = to_email
    msg["Subject"] = f"預訂成功！您的入場二維碼 - 訂單 #{order_number}"

    msg.attach(MIMEText(html, "html"))

    # Attach every QR code
    for slot in timeslots:
        qr_img = MIMEImage(slot["qr_png"])
        qr_img.add_header("Content-ID", f"<{slot['qr_cid']}>")
        msg.attach(qr_img)

    # Static images
    for data, cid in [(CLIENT_LOGO, "logo"), (CLIENT_MAP, "map"),
                      (IG_ICON, "ig_icon"), (FB_ICON, "fb_icon"), (WA_ICON, "wa_icon")]:
        img = MIMEImage(data)
        img.add_header("Content-ID", f"<{cid}>")
        msg.attach(img)

    get_smtp_connection().send_message(msg)
    logger.info("Email sent", extra={"to": to_email, "slots": len(timeslots)})

# -------------------------------------------------
# Main Handler
# -------------------------------------------------
def lambda_handler(event, context):
    logger.info("Lambda invoked")

    try:
        raw_body = event.get("body", "")
        if event.get("isBase64Encoded", False):
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        payload = json.loads(raw_body)

        # -------------------------------------------------
        # 1. 收集所有時段（支援多個）
        # -------------------------------------------------
        timeslots = []

        for item in payload.get("line_items", []):
            meta = item.get("meta_data", [])
            start_raw = extract_meta_value(meta, "phive_display_time_from")
            end_raw   = extract_meta_value(meta, "phive_display_time_to")

            if start_raw and end_raw:
                try:
                    dt_start = datetime.strptime(start_raw.strip(), "%d/%m/%Y %H:%M")
                    dt_end   = datetime.strptime(end_raw.strip(),   "%d/%m/%Y %H:%M")
                    entry_time = (dt_start - timedelta(minutes=10)).strftime("%d/%m/%Y %H:%M")

                    start_dt = dt_start.strftime("%Y%m%d%H%M%S")
                    end_dt   = dt_end.strftime("%Y%m%d%H%M%S")

                    qr_data = f"[,{AREA_ID},{start_dt},{end_dt},,,{QR_CODE_TYPE},]"
                    final_qr = "SK01" + (encrypt_aes_ecb(qr_data) if IS_ENCRYPT_QR_CODE else qr_data)
                    qr_png = generate_qr_png(final_qr)

                    timeslots.append({
                        "product_name": item.get("name", "場地預訂"),
                        "start_time": start_raw,
                        "end_time": end_raw,
                        "entry_time": entry_time,
                        "qr_png": qr_png,
                        "qr_cid": f"qr_{len(timeslots)}"
                    })
                except Exception as e:
                    logger.warning(f"Time parse error for item {item.get('id')}: {e}")

        # 後備：至少一張
        if not timeslots:
            fallback = generate_qr_png("SK01[,,20250101000000,20250101010000,,,,6,]")
            timeslots.append({
                "product_name": "場地預訂",
                "start_time": "01/01/2025 00:00",
                "end_time": "01/01/2025 01:00",
                "entry_time": "01/01/2025 00:00",
                "qr_png": fallback,
                "qr_cid": "qr_0"
            })

        # -------------------------------------------------
        # 2. 產生多張 QR code 的 HTML
        # -------------------------------------------------
        qr_blocks = ""
        for i, slot in enumerate(timeslots):
            qr_blocks += f"""
            <div style="margin:40px 0; padding:20px; background:#f8f9fa; border-radius:12px; text-align:center; border:2px solid #e0e0e0;">
                <h3 style="margin:0 0 12px; color:#2c3e50;">{slot['product_name']}</h3>
                <p style="margin:8px 0;"><strong>預訂時間：</strong>{slot['start_time']} 至 {slot['end_time']}</p>
                <p style="margin:8px 0;"><strong>可入場時間：</strong>
                    <span style="background:#fffacd; padding:4px 10px; border-radius:6px; font-weight:bold;">
                        {slot['entry_time']} 起
                    </span>
                </p>
                <img src="cid:{slot['qr_cid']}" alt="QR Code {i+1}" style="width:230px; height:230px; margin:15px auto; border:1px solid #ccc; padding:10px; background:white; border-radius:8px;">
                <p style="margin:10px 0 0; color:#e74c3c; font-weight:bold;">請出示此 QR code 入場</p>
            </div>
            """

        # -------------------------------------------------
        # 3. 產生訂單項目 HTML
        # -------------------------------------------------
        items_html = ""
        for item in payload.get("line_items", []):
            items_html += f"<tr><td>{item.get('name','')}</td><td>{item.get('quantity',0)}</td><td>{item.get('total','')} {payload.get('currency','')}</td></tr>"

        # -------------------------------------------------
        # 4. Render template
        # -------------------------------------------------
        billing = payload.get("billing", {})
        shipping = payload.get("shipping", {})
        order_number = payload.get("number", "N/A")

        html_body = HTML_TEMPLATE.format(
            first_name=billing.get("first_name", ""),
            last_name=billing.get("last_name", ""),
            order_number=order_number,
            date_created=payload.get("date_created", "")[:19].replace("T", " "),
            total=payload.get("total", "0"),
            currency=payload.get("currency", ""),
            line_items=items_html,
            billing_address=format_address(billing),
            shipping_address=format_address(shipping),
            payment_method=payload.get("payment_method_title", "N/A"),
            year=datetime.now().year,
            timeslot_count=len(timeslots),
            qr_codes_html=qr_blocks
        )

        # -------------------------------------------------
        # 5. Send
        # -------------------------------------------------
        customer_email = billing.get("email")
        if not customer_email:
            return {"statusCode": 400, "body": "No email"}

        send_email(customer_email, html_body, timeslots, order_number)

        return {"statusCode": 200, "body": "OK"}

    except Exception as e:
        logger.exception("Error")
        return {"statusCode": 200, "body": "OK"}