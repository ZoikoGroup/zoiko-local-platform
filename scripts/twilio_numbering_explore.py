"""
Stage 2 prep — standalone Twilio numbering exploration script.

Not part of the backend app on purpose: Stage 2 (Number Inventory + Twilio
integration) hasn't started yet, and the real wrapper belongs in
backend/app/integrations/telecom/twilio.py once it does (Provider Gateway
pattern — see CLAUDE.md). This script exists so whoever picks up Stage 2 has
working reference code instead of just curl notes; see
docs/stage2-twilio-numbering-notes.md for the findings this produced.

Reads TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN from the repo-root .env directly
(no python-dotenv dependency needed for a standalone script) so it never
touches backend/requirements.txt. Root .env, not backend/.env — matches
backend/app/core/config.py's env_file = "../.env" (relative to cwd=backend/
when running uvicorn per the README).

Usage:
    python scripts/twilio_numbering_explore.py search US local --area-code 628
    python scripts/twilio_numbering_explore.py search GB mobile
    python scripts/twilio_numbering_explore.py coverage US CA GB MX ZA NG KE GH
    python scripts/twilio_numbering_explore.py owned
"""

import argparse
import pathlib
import sys

import requests

BASE_URL = "https://api.twilio.com/2010-04-01"
ENV_PATH = pathlib.Path(__file__).resolve().parent.parent / ".env"


def load_credentials(env_path=ENV_PATH):
    values = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    sid = values.get("TWILIO_ACCOUNT_SID")
    token = values.get("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        raise RuntimeError(f"TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing from {env_path}")
    return sid, token


def _get(sid, token, path, params=None):
    resp = requests.get(f"{BASE_URL}/{path}", auth=(sid, token), params=params or {})
    return resp.status_code, resp.json()


def search_available_numbers(sid, token, country, number_type="local", area_code=None, contains=None, page_size=5):
    """Mirrors the draft POST /numbers/search contract in docs/stage2-twilio-numbering-notes.md.

    Twilio behavior confirmed by hand: unsupported country -> 404 with a
    Twilio error body; valid country + no matches -> 200 with an empty list.
    Neither is a Python exception here — both come back as plain results so
    the eventual FastAPI endpoint can decide how to translate them.
    """
    params = {"PageSize": page_size}
    if area_code:
        params["AreaCode"] = area_code
    if contains:
        params["Contains"] = contains

    type_path = {"local": "Local", "mobile": "Mobile", "tollfree": "TollFree"}[number_type]
    status, body = _get(sid, token, f"Accounts/{sid}/AvailablePhoneNumbers/{country}/{type_path}.json", params)

    if status == 404:
        return {"supported": False, "results": []}
    if status != 200:
        return {"supported": None, "error": body}

    results = [
        {
            "phone_number": n["phone_number"],
            "locality": n.get("locality"),
            "region": n.get("region"),
            "capabilities": n["capabilities"],
            "address_requirements": n["address_requirements"],
        }
        for n in body["available_phone_numbers"]
    ]
    return {"supported": True, "results": results}


def list_owned_numbers(sid, token):
    status, body = _get(sid, token, f"Accounts/{sid}/IncomingPhoneNumbers.json")
    if status != 200:
        return {"error": body}
    return body["incoming_phone_numbers"]


def check_market_coverage(sid, token, countries, number_type="local"):
    """Reproduces the launch-market coverage table in the notes doc, live."""
    report = {}
    for country in countries:
        result = search_available_numbers(sid, token, country, number_type=number_type, page_size=1)
        if not result["supported"]:
            report[country] = "NOT SUPPORTED"
        elif result["results"]:
            caps = result["results"][0]["capabilities"]
            addr = result["results"][0]["address_requirements"]
            report[country] = f"available (voice={caps['voice']} sms={caps['SMS']} mms={caps['MMS']}, address_requirements={addr})"
        else:
            report[country] = "supported, but no numbers matched this query"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="Search available numbers in one country")
    p_search.add_argument("country", help="ISO country code, e.g. US, GB")
    p_search.add_argument("number_type", choices=["local", "mobile", "tollfree"], nargs="?", default="local")
    p_search.add_argument("--area-code")
    p_search.add_argument("--contains")

    p_coverage = sub.add_parser("coverage", help="Check numbering coverage across multiple countries")
    p_coverage.add_argument("countries", nargs="+", help="ISO country codes, e.g. US CA GB NG KE GH")
    p_coverage.add_argument("--number-type", choices=["local", "mobile", "tollfree"], default="local")

    sub.add_parser("owned", help="List numbers currently owned on the account")

    args = parser.parse_args()
    sid, token = load_credentials()

    if args.command == "search":
        result = search_available_numbers(sid, token, args.country, args.number_type, args.area_code, args.contains)
        if not result["supported"]:
            print(f"{args.country}/{args.number_type}: NOT SUPPORTED by Twilio")
        else:
            for n in result["results"]:
                print(n)
            if not result["results"]:
                print("(no matching numbers — country/type is supported, query just had no hits)")

    elif args.command == "coverage":
        report = check_market_coverage(sid, token, args.countries, args.number_type)
        for country, status in report.items():
            print(f"{country}: {status}")

    elif args.command == "owned":
        owned = list_owned_numbers(sid, token)
        if not owned:
            print("(no numbers currently owned on this account)")
        for n in owned:
            print(n)


if __name__ == "__main__":
    sys.exit(main())
