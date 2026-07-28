"""Provider-thin transactional email sender (Resend HTTP API).

One function, one provider call. Callers treat a False return as "mail didn't
go out" and answer a clean 502 — a mail failure must never crash an auth flow.
DEMO_MODE never touches the network: the full text body is printed to stdout
with a grep-able "DEMO-MAIL to=<addr>" prefix (mirrors the DemoClient
stage-prop pattern), so the sandbox signup/reset flows can be walked
end-to-end by reading the server logs.
"""
from __future__ import annotations

import requests

from ..config import settings
from ..db import audit

_RESEND_URL = "https://api.resend.com/emails"


def send(to: str, subject: str, html: str, text: str) -> bool:
    """Deliver one email. True = accepted by the provider (or demo-printed).

    False on ANY failure — the requests/status detail is audited (kind
    "mail_error"; never the message body, which may carry a login code).
    """
    # Demo accounts are still accounts — the public demo (which HAS a mail key)
    # sends real verification emails exactly like the club. The stdout print is
    # only for keyless local sandboxes, where the terminal is the inbox.
    if settings.demo_mode and not settings.resend_api_key:
        print(f"DEMO-MAIL to={to} subject={subject}\n{text}", flush=True)
        return True
    if not settings.resend_api_key:
        return False   # inert without a key; callers gate on settings.email_enabled
    try:
        r = requests.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.mail_from, "to": [to],
                  "subject": subject, "html": html, "text": text},
            timeout=10,
        )
    except requests.RequestException as e:
        audit("mailer", "mail_error", to, {"error": str(e)[:300]})
        return False
    if r.status_code >= 300:
        audit("mailer", "mail_error", to, {"status": r.status_code, "body": r.text[:300]})
        return False
    return True
