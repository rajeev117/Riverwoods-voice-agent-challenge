
import os
from dotenv import load_dotenv
from twilio.rest import Client

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_BASE_DIR, ".env"))

account_sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
auth_token = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
from_number = (os.environ.get("TWILIO_PHONE_NUMBER") or "").strip()
status_callback_url = (os.environ.get("TWILIO_STATUS_CALLBACK_URL") or "").strip()

if not account_sid or not auth_token or not from_number:
    raise SystemExit("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER must be set")

client = Client(account_sid, auth_token)

def send_sms(to, message):
    m = client.messages.create(body=message, from_=from_number, to=to)
    return {"sid": m.sid, "status": m.status}

def make_call(to, message="", twiml_url="", machine_detection=True):
    create_kwargs = {
        "to": to,
        "from_": from_number,
    }
    if status_callback_url:
        create_kwargs.update({
            "status_callback": status_callback_url,
            "status_callback_event": ["initiated", "ringing", "answered", "completed"],
            "status_callback_method": "POST",
        })

    if machine_detection:
        create_kwargs["machine_detection"] = "Enable"
        create_kwargs["machine_detection_timeout"] = 5

    if twiml_url:
        create_kwargs["url"] = twiml_url
        c = client.calls.create(**create_kwargs)
    else:
        twiml = f"<Response><Say>{message or 'Hello'}</Say></Response>"
        create_kwargs["twiml"] = twiml
        c = client.calls.create(**create_kwargs)
    return {"sid": c.sid, "status": c.status}
