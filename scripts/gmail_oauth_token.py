"""One-time OAuth consent that gets a Gmail refresh token for the send-email hook.

Writes the Gmail secrets to supabase/functions/.env (git-ignored) without printing them.
Uses only the standard library.

Usage:
    python scripts/gmail_oauth_token.py --client-file <client_secret.json> \
        --sender <system gmail> [--test-to <recipient>]
"""

import argparse
import base64
import hashlib
import json
import secrets
import sys
import webbrowser
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
ENV_PATH = Path(__file__).resolve().parents[1] / "supabase" / "functions" / ".env"
HOOK_SECRET_KEY = "SEND_EMAIL_HOOK_SECRET"
CONSENT_TIMEOUT_SECONDS = 300


def load_client(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "installed" not in data:
        sys.exit("The OAuth client must be a 'Desktop app'. Create one and download its JSON again.")
    return data["installed"]["client_id"], data["installed"]["client_secret"]


def wait_for_code(server: HTTPServer, state: str) -> str:
    """Serve the loopback redirect until Google returns a code for this attempt."""
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        # Browsers may open idle preconnect sockets; drop them instead of blocking forever.
        timeout = 10

        def do_GET(self) -> None:
            query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            # Ignore favicon requests and redirects that do not belong to this attempt.
            if query.get("state") != state:
                self.send_error(404)
                return
            result.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and return to the terminal.")

        def log_message(self, *args: object) -> None:
            pass  # The default log line would print the authorization code.

    server.RequestHandlerClass = Handler
    server.timeout = CONSENT_TIMEOUT_SECONDS
    server.handle_timeout = lambda: sys.exit("Timed out waiting for the consent screen.")
    while not result:
        server.handle_request()
    if "code" not in result:
        sys.exit(f"Consent failed: {result.get('error', 'no code returned')}")
    return result["code"]


def post(url: str, data: bytes, headers: dict[str, str]) -> dict:
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        sys.exit(f"Request to {url} failed: {error.code} {error.read().decode(errors='replace')}")


def post_token(fields: dict[str, str]) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    return post(TOKEN_URL, urlencode(fields).encode(), headers)


def authorize(client_id: str, client_secret: str, sender: str) -> dict:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    state = secrets.token_urlsafe(24)

    with HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}"
        url = f"{AUTH_URL}?" + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
                "login_hint": sender,
            }
        )
        print(f"Sign in as {sender}. If the browser does not open, visit:\n{url}\n", flush=True)
        webbrowser.open(url)
        code = wait_for_code(server, state)

    tokens = post_token(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    )
    if SCOPE not in tokens.get("scope", "").split():
        sys.exit("The gmail.send permission was not granted. Run again and allow it.")
    if "refresh_token" not in tokens:
        sys.exit("Google returned no refresh token. Run again.")
    return tokens


def write_env(path: Path, values: dict[str, str]) -> None:
    """Update the given keys and keep every other line, including the hook secret."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0] not in values]
    kept += [f"{key}={value}" for key, value in values.items()]
    if not any(line.split("=", 1)[0] == HOOK_SECRET_KEY for line in kept):
        kept.append(f"{HOOK_SECRET_KEY}=")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def send_test(access_token: str, sender: str, recipient: str) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "V-Lookup: kiểm tra Gmail API"
    message.set_content("Gmail API đã gửi được email.")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    post(SEND_URL, json.dumps({"raw": raw}).encode(), headers)
    print(f"Test email sent to {recipient}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client-file", required=True, type=Path, help="Desktop OAuth client JSON")
    parser.add_argument("--sender", required=True, help="Gmail address that sends the emails")
    parser.add_argument("--test-to", help="also send a test email to this address")
    args = parser.parse_args()

    client_id, client_secret = load_client(args.client_file)
    tokens = authorize(client_id, client_secret, args.sender)
    write_env(
        ENV_PATH,
        {
            "GMAIL_CLIENT_ID": client_id,
            "GMAIL_CLIENT_SECRET": client_secret,
            "GMAIL_REFRESH_TOKEN": tokens["refresh_token"],
            "GMAIL_SENDER": args.sender,
        },
    )
    print(f"Saved Gmail secrets to {ENV_PATH}")
    if args.test_to:
        send_test(tokens["access_token"], args.sender, args.test_to)


if __name__ == "__main__":
    main()
