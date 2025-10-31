import os
import json
import hmac
import hashlib
import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
from jinja2 import Template

# -------------------------------------------------
# CONFIG (Lambda environment variables)
# -------------------------------------------------
GMAIL_USER = os.environ["GMAIL_USER"]                    # e.g. orders@yourstore.com
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]    # 16-char App Password
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

# Load template at cold start
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template.html")
with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    EMAIL_TEMPLATE = Template(f.read())

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# -------------------------------------------------
def validate_signature(body: bytes, signature: str, secret: str) -> bool:
    """WooCommerce: X-WC-Webhook-Signature = sha256=BASE64_HMAC"""
    if not signature or not secret:
        return False
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    expected = "sha256=" + base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)

# -------------------------------------------------
def render_email(context: dict) -> tuple[str, str]:
    html = EMAIL_TEMPLATE.render(**context)
    text = f"""Hello {context['billing']['first_name']} {context['billing']['last_name']},

Thank you for your order #{context['order_number']} placed on {context['date_created']}.

Total: {context['total']} {context['currency']}

We’ll ship it soon. Reply to this email with any questions.

Best,
Your Store Team
"""
    return html, text.strip()

# -------------------------------------------------
def send_via_gmail(to_email: str, html_body: str, text_body: str):
    msg = MIMEMultipart("alternative")
    msg["From"] = GMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = "Thank you for your order!"

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        logger.info(f"Email sent to {to_email}")
    except smtplib.SMTPException as e:
        logger.error(f"Gmail SMTP error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected send error: {e}")
        raise

# -------------------------------------------------
def lambda_handler(event, context):
    logger.info("=== LAMBDA INVOKED ===")
    logger.info("FULL EVENT", extra={"event": event})
    logger.info(event)
    logger.info("CONTEXT", extra={"request_id": context.aws_request_id if context else None})
    logger.info(context.aws_request_id if context else None)

    try:
        # 1. Extract raw body
        raw_body = event["body"]
        if event.get("isBase64Encoded", False):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        # 2. Parse JSON
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON payload")
            return {"statusCode": 400, "body": "Bad JSON"}

        # # 3. Validate signature
        # signature = event["headers"].get("x-wc-webhook-signature") or \
        #             event["headers"].get("X-WC-Webhook-Signature")
        # if not validate_signature(raw_body.encode("utf-8"), signature, WEBHOOK_SECRET):
        #     logger.warning("Invalid webhook signature")
        #     return {"statusCode": 401, "body": "Unauthorized"}

        # 4. Check topic
        topic = event["headers"].get("x-wc-webhook-topic") or \
                event["headers"].get("X-WC-Webhook-Topic")
        if topic != "order.created":
            logger.info(f"Ignored topic: {topic}")
            return {"statusCode": 200, "body": "Ignored"}

        # 5. Build template context
        context = {
            "order_number": payload.get("number"),
            "date_created": payload.get("date_created", "")[:19].replace("T", " "),
            "total": payload.get("total"),
            "currency": payload.get("currency"),
            "billing": payload.get("billing", {}),
            "line_items": [
                {
                    "name": item.get("name"),
                    "quantity": item.get("quantity"),
                    "total": item.get("total"),
                }
                for item in payload.get("line_items", [])
            ],
        }

        # 6. Render email
        html_body, text_body = render_email(context)

        # 7. Send
        customer_email = payload["billing"]["email"]
        send_via_gmail(customer_email, html_body, text_body)

        return {"statusCode": 200, "body": "OK"}

    except Exception as e:
        logger.exception("Lambda failed")
        return {"statusCode": 500, "body": "Internal Error"}