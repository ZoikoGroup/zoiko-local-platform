"""
Stage 3 prep — standalone Twilio Voice (calling) exploration script.

Same rationale as scripts/twilio_numbering_explore.py: Stage 3 (Voice Routing,
backend/app/media/) can't start until Stages 1-2 land, so this is reference
code + live findings, not app code. Lives outside backend/app/ on purpose.

Important limitation found while writing this: placing a real outbound call
via /Calls.json requires an owned Twilio number as `From`. This account owns
zero numbers (see twilio_numbering_explore.py findings), so `place_call()`
below is written against Twilio's documented Calls API contract but has NOT
been executed live. `list_calls()` and `get_call()` are read-only and were
tested live successfully with zero owned numbers / zero calls made.

Usage:
    python scripts/twilio_calling_explore.py list
    python scripts/twilio_calling_explore.py call --to +916305101934 --from +1XXXXXXXXXX --twiml-url https://example.com/twiml
    python scripts/twilio_calling_explore.py status <call_sid>
"""

import argparse
import sys

import requests

from twilio_numbering_explore import BASE_URL, load_credentials


def _get(sid, token, path, params=None):
    resp = requests.get(f"{BASE_URL}/{path}", auth=(sid, token), params=params or {})
    return resp.status_code, resp.json()


def _post(sid, token, path, data):
    resp = requests.post(f"{BASE_URL}/{path}", auth=(sid, token), data=data)
    return resp.status_code, resp.json()


def list_calls(sid, token, limit=5):
    """Read-only. Tested live: returns 200 with an empty list on an account
    that owns no numbers and has made no calls — not an error condition,
    consistent with the empty-result behavior found in the numbering search API.
    """
    status, body = _get(sid, token, f"Accounts/{sid}/Calls.json", {"PageSize": limit})
    if status != 200:
        return {"error": body}
    return body["calls"]


def get_call(sid, token, call_sid):
    status, body = _get(sid, token, f"Accounts/{sid}/Calls/{call_sid}.json")
    if status != 200:
        return {"error": body}
    return body


def place_call(sid, token, to, from_, twiml_url=None, twiml=None):
    """NOT executed live in this account — no owned Twilio number to call
    `from`. Written directly against Twilio's documented Calls API contract:
    https://www.twilio.com/docs/voice/api/call-resource#create-a-call-resource

    `from_` MUST be a Twilio number owned on this account (not a verified
    caller ID — that's only for the reverse direction, receiving trial
    calls/SMS). Exactly one of `twiml_url` or `twiml` is required: Twilio
    fetches TwiML instructions from a URL it calls back, or takes inline
    TwiML directly via the `Twiml` param.
    """
    if not twiml_url and not twiml:
        raise ValueError("Provide either twiml_url or twiml")

    data = {"To": to, "From": from_}
    if twiml_url:
        data["Url"] = twiml_url
    else:
        data["Twiml"] = twiml

    status, body = _post(sid, token, f"Accounts/{sid}/Calls.json", data)
    if status not in (200, 201):
        return {"error": body}
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List call logs (read-only, works with zero owned numbers)")

    p_status = sub.add_parser("status", help="Get details for one call by SID")
    p_status.add_argument("call_sid")

    p_call = sub.add_parser("call", help="Place an outbound call (requires an owned Twilio number)")
    p_call.add_argument("--to", required=True)
    p_call.add_argument("--from", dest="from_", required=True)
    p_call.add_argument("--twiml-url")
    p_call.add_argument("--twiml", help='Inline TwiML, e.g. "<Response><Say>Hello</Say></Response>"')

    args = parser.parse_args()
    sid, token = load_credentials()

    if args.command == "list":
        calls = list_calls(sid, token)
        if not calls:
            print("(no calls on this account yet)")
        for c in calls:
            print(c)

    elif args.command == "status":
        print(get_call(sid, token, args.call_sid))

    elif args.command == "call":
        result = place_call(sid, token, args.to, args.from_, args.twiml_url, args.twiml)
        print(result)


if __name__ == "__main__":
    sys.exit(main())
