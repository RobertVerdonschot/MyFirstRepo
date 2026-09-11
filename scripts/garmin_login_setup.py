#!/usr/bin/env python3
"""One-time interactive Garmin login.

Run this yourself in a real terminal (Cloud Shell is fine) so it can prompt
for your MFA code if you have that enabled:

    python scripts/garmin_login_setup.py [tokenstore-dir]

It logs in with your Garmin Connect credentials and saves the resulting
session tokens to the given directory (default: ./garmin_tokens). Nothing in
this session ever runs inside Cloud Run -- afterwards, run
scripts/pack_and_upload_garmin_tokens.sh to upload the result to Secret
Manager, which is what the deployed bot actually reads. Your Garmin password
is never uploaded or deployed anywhere.
"""

from __future__ import annotations

import sys
from getpass import getpass

from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError


def main() -> None:
    tokenstore = sys.argv[1] if len(sys.argv) > 1 else "garmin_tokens"

    email = input("Garmin e-mail: ").strip()
    password = getpass("Garmin wachtwoord: ")

    garmin = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("MFA-code: ").strip(),
    )
    try:
        garmin.login(tokenstore)
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
        print(f"Inloggen mislukt: {exc}")
        sys.exit(1)

    print(f"Ingelogd. Tokens opgeslagen in: {tokenstore}")
    print("Volgende stap: ./scripts/pack_and_upload_garmin_tokens.sh " + tokenstore + " <gcp-project-id>")


if __name__ == "__main__":
    main()
