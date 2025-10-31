import os
import json
import hmac
import hashlib
import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

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
<html>
<head><meta charset="utf-8"><title>Thank you!</title></head>
<body style="font-family:Arial; color:#333;">
  <h2>Hello {first_name} {last_name},</h2>
  <p>Thank you for your order <strong>#{order_number}</strong> on {date_created}.</p>

  <h3>Booking Time</h3>
  <p><strong>Start:</strong> {start_time}</p>
  <p><strong>End:</strong> {end_time}</p>

  <h3>Order Summary</h3>
  <table border="0" cellpadding="6" style="border-collapse:collapse; width:100%;">
    <thead><tr style="background:#f9f9f9;"><th align="left">Item</th><th align="right">Qty</th><th align="right">Price</th></tr></thead>
    <tbody>{line_items}</tbody>
    <tfoot><tr><td colspan="2" align="right"><strong>Total:</strong></td><td align="right"><strong>{total} {currency}</strong></td></tr></tfoot>
  </table>

  <p>We’ll confirm your booking soon. Reply with questions.</p>
  <p>Best,<br>Your Store</p>
</body>
</html>
""".strip()

# -------------------------------------------------
def extract_meta_value(meta_data, key):
    for meta in meta_data:
        if meta.get("key") == key:
            value = meta.get("value", [])
            return value[0] if isinstance(value, list) and value else value
    return "N/A"

# -------------------------------------------------
def validate_signature(raw_body: str, signature: str) -> bool:
    if not signature or not WEBHOOK_SECRET:
        logger.warning("Missing signature/secret")
        return False
    mac = hmac.new(WEBHOOK_SECRET.encode(), raw_body.encode(), hashlib.sha256)
    expected = "sha256=" + base64.b64encode(mac.digest()).decode()
    logger.info("Signature")
    logger.info("received")
    logger.info(signature)
    logger.info("expected")
    logger.info(expected)
    return hmac.compare_digest(expected, signature)

# -------------------------------------------------
def send_email(to_email: str, html: str, text: str):
    msg = MIMEMultipart("alternative")
    msg["From"] = GMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = "Booking Confirmed – Thank You!"
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            # FIX: Strip whitespace & force ASCII-safe password
            clean_user = GMAIL_USER.strip()
            clean_pass = GMAIL_APP_PASSWORD.strip()

            logger.info("SMTP login attempt")
            logger.info("user")
            logger.info(clean_user)

            server.login(clean_user, clean_pass)
            server.send_message(msg)
        logger.info("Email sent")
        logger.info("to")
        logger.info(to_email)
    except Exception as e:
        logger.error("SMTP send failed")
        logger.error("error")
        logger.error(str(e))
        logger.error("type")
        logger.error(type(e).__name__)
        raise

# -------------------------------------------------
def lambda_handler(event, context):
    logger.info("=== LAMBDA START ===")
    logger.info("FULL EVENT")
    logger.info(event)
    logger.info("CONTEXT")
    logger.info(context.aws_request_id if context else None)

    try:
        raw_body = event.get("body", "")
        if event.get("isBase64Encoded", False):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        logger.info("RAW BODY")
        logger.info(len(raw_body))

        payload = json.loads(raw_body)
        logger.info("Order parsed", extra={"id": payload.get("id")})
        logger.info({"id": payload.get("id")})

        # Signature
        signature = (
            event.get("headers", {}).get("x-wc-webhook-signature") or
            event.get("headers", {}).get("X-WC-Webhook-Signature")
        )
        # if signature and not validate_signature(raw_body, signature):
        #     logger.warning("Invalid signature")
        #     return {"statusCode": 401, "body": "Unauthorized"}

        # Customer email
        billing = payload.get("billing", {})
        customer_email = billing.get("email")
        if not customer_email:
            logger.warning("No email")
            return {"statusCode": 400, "body": "No email"}

        # Extract booking times
        start_time = "N/A"
        end_time = "N/A"
        for item in payload.get("line_items", []):
            meta = item.get("meta_data", [])
            start_time = extract_meta_value(meta, "phive_display_time_from") or start_time
            end_time = extract_meta_value(meta, "phive_display_time_to") or end_time
            if start_time != "N/A" and end_time != "N/A":
                break

        logger.info("Booking")
        logger.info("start")
        logger.info(start_time)
        logger.info("end")
        logger.info(end_time)

        # Order items HTML
        items_html = ""
        for item in payload.get("line_items", []):
            items_html += f"<tr><td>{item.get('name','')}</td><td align='right'>{item.get('quantity',0)}</td><td align='right'>{item.get('total','')} {payload.get('currency','')}</td></tr>"

        # Render email
        html_body = HTML_TEMPLATE.format(
            first_name=billing.get("first_name", ""),
            last_name=billing.get("last_name", ""),
            order_number=payload.get("number", "N/A"),
            date_created=payload.get("date_created", "")[:19].replace("T", " "),
            start_time=start_time,
            end_time=end_time,
            total=payload.get("total", "0"),
            currency=payload.get("currency", ""),
            line_items=items_html
        )

        text_body = f"Hello {billing.get('first_name')} {billing.get('last_name')},\n\n" \
                    f"Booking confirmed!\n" \
                    f"Start: {start_time}\n" \
                    f"End: {end_time}\n" \
                    f"Order #{payload.get('number')} – Total: {payload.get('total')} {payload.get('currency')}\n\n" \
                    "We’ll see you soon!"

        # Send
        send_email(customer_email, html_body, text_body)

        logger.info("=== SUCCESS ===")
        return {"statusCode": 200, "body": "OK"}

    except Exception as e:
        logger.exception("LAMBDA ERROR")
        return {"statusCode": 500, "body": "Error"}