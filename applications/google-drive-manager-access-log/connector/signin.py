"""One-off Google sign-in for the Ekaya Drive audit connector.

Runs the OAuth loopback flow with PKCE for a Desktop client, requests only the read-only
audit-log scope (plus the account's email, to show who signed in), and saves client_id,
client_secret and refresh_token to a file readable only by the owner. It never prints a secret
or a token.

Usage: python3 -I google_audit_signin.py CLIENT_JSON OUTPUT_JSON
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPES = " ".join([
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
])
WAIT_SECONDS = 270

client_file, out_file = sys.argv[1], sys.argv[2]
with open(client_file, encoding="utf-8") as stream:
    client = json.load(stream)["installed"]

verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
state = secrets.token_urlsafe(16)
received = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in query or "error" in query:
            received.update({k: v[0] for k, v in query.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Signed in. You can close this tab and return to Claude Code.")

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
server.timeout = 5
redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": client["client_id"],
    "redirect_uri": redirect_uri,
    "response_type": "code",
    "scope": SCOPES,
    "access_type": "offline",
    "prompt": "consent",
    "state": state,
    "code_challenge": challenge,
    "code_challenge_method": "S256",
})
print("Opening Google's sign-in page in your browser. If it doesn't open, use this address:")
print(auth_url, flush=True)
webbrowser.open(auth_url)

deadline = time.monotonic() + WAIT_SECONDS
while not received and time.monotonic() < deadline:
    server.handle_request()
server.server_close()

if not received:
    sys.exit("Timed out waiting for the browser sign-in. Run it again.")
if "error" in received:
    sys.exit(f"Google returned an error: {received['error']}")
if received.get("state") != state:
    sys.exit("The sign-in response did not match this request. Run it again.")

body = urllib.parse.urlencode({
    "code": received["code"],
    "client_id": client["client_id"],
    "client_secret": client["client_secret"],
    "redirect_uri": redirect_uri,
    "grant_type": "authorization_code",
    "code_verifier": verifier,
}).encode()
try:
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body), timeout=30) as response:
        token = json.load(response)
except urllib.error.HTTPError as err:
    detail = json.load(err)
    sys.exit(f"Token exchange failed: {detail.get('error')}: {detail.get('error_description')}")

if not token.get("refresh_token"):
    sys.exit("Google returned no refresh token. Run it again.")

request = urllib.request.Request("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": "Bearer " + token["access_token"]})
with urllib.request.urlopen(request, timeout=30) as response:
    email = json.load(response).get("email")

os.makedirs(os.path.dirname(out_file), mode=0o700, exist_ok=True)
fd = os.open(out_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    json.dump({
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "refresh_token": token["refresh_token"],
    }, stream)

print(f"Signed in as: {email}")
print("Scopes granted:")
for scope in token.get("scope", "").split():
    print("  " + scope)
print(f"Saved to {out_file} (readable only by you). Nothing secret was printed.")
