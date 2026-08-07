"""
Standalone script to test the full OAuth + Copilot API flow.
Run: python scripts/test_oauth_flow.py

1. Opens browser to authorize
2. Captures callback on localhost:3000
3. Exchanges code for token
4. Tests Copilot API immediately (before token expires)
"""

import asyncio
import base64
import hashlib
import json
import secrets
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

import aiohttp

# Config
DEPLOYMENT_URL = "https://epddata.stag.sumologic.net"
CIMD_METADATA_URL = "https://naveenrama.github.io/slack-integrates-to-mobot/oauth-client-metadata.json"
CALLBACK_URL = "http://localhost:3000/oauth/callback"
CALLBACK_PORT = 3000

# PKCE
verifier = secrets.token_urlsafe(64)
digest = hashlib.sha256(verifier.encode()).digest()
challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
state = secrets.token_urlsafe(32)

# Build authorize URL
params = {
    "response_type": "code",
    "client_id": CIMD_METADATA_URL,
    "redirect_uri": CALLBACK_URL,
    "state": state,
    "code_challenge": challenge,
    "code_challenge_method": "S256",
}
authorize_url = f"{DEPLOYMENT_URL}/oauth2/authorize?{urlencode(params)}"

print("=" * 60)
print("OAUTH TEST FLOW")
print("=" * 60)
print(f"\nDeployment: {DEPLOYMENT_URL}")
print(f"CIMD URL:   {CIMD_METADATA_URL}")
print(f"\nOpening browser to authorize...")
print(f"URL: {authorize_url}\n")

# Capture the callback
auth_code = None

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "error" in params:
            print(f"\nOAuth ERROR: {params['error'][0]}")
            print(f"Description: {params.get('error_description', [''])[0]}")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"OAuth error. Check terminal.")
            return

        if "code" in params:
            received_state = params.get("state", [""])[0]
            if received_state != state:
                print("\nState mismatch!")
                self.send_response(400)
                self.end_headers()
                return

            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Got it! Check terminal for results.")
            print(f"\nReceived auth code: {auth_code[:20]}...")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


# Open browser
webbrowser.open(authorize_url)

# Wait for callback
print("Waiting for callback on :3000...")
server = HTTPServer(("", CALLBACK_PORT), CallbackHandler)
server.handle_request()  # Handle one request then stop
server.server_close()

if not auth_code:
    print("\nNo auth code received. Exiting.")
    exit(1)


# Exchange code for token
async def exchange_and_test():
    print("\n" + "=" * 60)
    print("TOKEN EXCHANGE")
    print("=" * 60)

    token_url = f"{DEPLOYMENT_URL}/oauth2/token"
    payload = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": CALLBACK_URL,
        "client_id": CIMD_METADATA_URL,
        "code_verifier": verifier,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(token_url, data=payload) as resp:
            print(f"\nToken exchange status: {resp.status}")
            if resp.status != 200:
                body = await resp.text()
                print(f"Error: {body}")
                return
            token_data = await resp.json()

        access_token = token_data["access_token"]
        print(f"Access token: {access_token[:30]}...")
        print(f"Expires in: {token_data.get('expires_in')} seconds")
        print(f"Scopes: {token_data.get('scope', 'not specified')}")

        # Decode JWT
        parts = access_token.split(".")
        jwt_payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        print(f"\nJWT audience: {jwt_payload.get('aud')}")
        print(f"JWT subject: {jwt_payload.get('sub')}")
        print(f"JWT issuer: {jwt_payload.get('iss')}")

        # Test APIs
        print("\n" + "=" * 60)
        print("API TESTS (using fresh token)")
        print("=" * 60)

        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # Test 1: Standard API on deployment URL
        print(f"\n--- {DEPLOYMENT_URL}/api/v2/content/folders/personal ---")
        async with session.get(f"{DEPLOYMENT_URL}/api/v2/content/folders/personal", headers=headers) as resp:
            print(f"Status: {resp.status}")
            body = await resp.text()
            print(f"Body: {body[:200]}")

        # Test 2: Standard API on audience URL
        aud_url = jwt_payload["aud"][0] if jwt_payload.get("aud") else DEPLOYMENT_URL
        if aud_url != DEPLOYMENT_URL:
            print(f"\n--- {aud_url}/api/v2/content/folders/personal ---")
            async with session.get(f"{aud_url}/api/v2/content/folders/personal", headers=headers) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:200]}")

        # Test 3: Copilot on deployment URL
        print(f"\n--- {DEPLOYMENT_URL}/api/v1/copilot/conversation ---")
        async with session.post(f"{DEPLOYMENT_URL}/api/v1/copilot/conversation", json={}, headers=headers) as resp:
            print(f"Status: {resp.status}")
            body = await resp.text()
            print(f"Body: {body[:200]}")

        # Test 4: Copilot on audience URL
        if aud_url != DEPLOYMENT_URL:
            print(f"\n--- {aud_url}/api/v1/copilot/conversation ---")
            async with session.post(f"{aud_url}/api/v1/copilot/conversation", json={}, headers=headers) as resp:
                print(f"Status: {resp.status}")
                body = await resp.text()
                print(f"Body: {body[:200]}")

        # Test 5: If copilot fails, try with apisession header
        print(f"\n--- {DEPLOYMENT_URL}/api/v1/copilot/conversation (apisession header) ---")
        async with session.post(
            f"{DEPLOYMENT_URL}/api/v1/copilot/conversation",
            json={},
            headers={"apisession": access_token, "Content-Type": "application/json"},
        ) as resp:
            print(f"Status: {resp.status}")
            body = await resp.text()
            print(f"Body: {body[:200]}")

        print("\n" + "=" * 60)
        print("DONE")
        print("=" * 60)


asyncio.run(exchange_and_test())
