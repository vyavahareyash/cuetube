"""Helper script to obtain Google OAuth 2.0 refresh token for YouTube Data API v3."""

import argparse
import json
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


def load_credentials_from_json(json_path: Path) -> tuple[str, str, str]:
    """Parse client_id, client_secret, and redirect_uri from downloaded Google OAuth JSON."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    # Google downloads either {"installed": {...}} or {"web": {...}}
    config = data.get("installed") or data.get("web") or data
    client_id = config.get("client_id")
    client_secret = config.get("client_secret")

    if not client_id or not client_secret:
        raise ValueError(f"Could not find client_id or client_secret in {json_path}")

    redirect_uris = config.get("redirect_uris", [])
    redirect_uri = (
        redirect_uris[0] if redirect_uris else "https://oauth.pstmn.io/v1/browser-callback"
    )

    return client_id, client_secret, redirect_uri


def update_env_file(env_path: Path, updates: dict[str, str]) -> None:
    """Update or append keys in .env file without deleting existing values or comments."""
    existing_lines: list[str] = []
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            existing_lines = f.readlines()

    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in updates:
                new_lines.append(f'{key}="{updates[key]}"\n')
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys that weren't present in the existing file
    remaining_keys = [k for k in updates if k not in updated_keys]
    if remaining_keys:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        for k in remaining_keys:
            new_lines.append(f'{k}="{updates[k]}"\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/calendar",
]


def get_tokens(
    client_id: str,
    client_secret: str,
    redirect_uri: str = "https://oauth.pstmn.io/v1/browser-callback",
    scopes: list[str] | None = None,
) -> None:
    scope = " ".join(scopes or DEFAULT_SCOPES)

    # Step 1: Authorization URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)

    print("\n1. Opening browser for authorization (or copy URL):")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    # Step 2: Prompt for code
    print(f"\n2. After granting access, you will be redirected to: {redirect_uri}?code=...")
    print("   Copy the code from the address bar (the value after 'code=').")
    auth_code = input("\nPaste the authorization code here: ").strip()

    # Step 3: Exchange code for refresh token
    token_url = "https://oauth2.googleapis.com/token"
    token_data = urllib.parse.urlencode(
        {
            "code": auth_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")

    req = urllib.request.Request(token_url, data=token_data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            refresh_token = data.get("refresh_token")
            print("\n✅ Successfully retrieved tokens!")
            print(f"REFRESH_TOKEN: {refresh_token}")

            updates = {
                "YOUTUBE_CLIENT_ID": client_id,
                "YOUTUBE_CLIENT_SECRET": client_secret,
                "GOOGLE_CLIENT_ID": client_id,
                "GOOGLE_CLIENT_SECRET": client_secret,
            }
            if refresh_token:
                updates["YOUTUBE_REFRESH_TOKEN"] = refresh_token
                updates["GOOGLE_REFRESH_TOKEN"] = refresh_token

            env_file = Path(".env")
            update_env_file(env_file, updates)
            print(f"\n💾 Saved credentials to {env_file.resolve()}")
    except urllib.error.HTTPError as exc:
        print(f"❌ Token exchange failed: {exc.read().decode('utf-8')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube OAuth Token Generator")
    parser.add_argument(
        "--credentials-file",
        "-f",
        default="credentials/client_secret.json",
        help="Path to downloaded client_secret.json (default: credentials/client_secret.json)",
    )
    parser.add_argument("--client-id", help="Google OAuth Client ID")
    parser.add_argument("--client-secret", help="Google OAuth Client Secret")
    args = parser.parse_args()

    cred_file = Path(args.credentials_file)
    if cred_file.exists():
        cid, csecret, red_uri = load_credentials_from_json(cred_file)
        get_tokens(cid, csecret, red_uri)
    elif args.client_id and args.client_secret:
        get_tokens(args.client_id, args.client_secret)
    else:
        print(f"Error: Credentials file '{cred_file}' not found, and client credentials omitted.")
        print(f"Place your JSON file at '{cred_file}' or specify via --credentials-file <path>")
