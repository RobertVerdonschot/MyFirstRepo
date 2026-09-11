#!/usr/bin/env python3
"""One-time interactive Garmin login.

Run this locally (with a real terminal, so it can prompt for your MFA code
if you have that enabled): `python scripts/garmin_login_setup.py`

It logs in with your Garmin Connect credentials and saves the resulting
session tokens to GARMIN_TOKENSTORE (default: data/garmin_tokens). The bot
process only ever reads that token file afterwards -- it never needs your
password and can't prompt for MFA, so this step has to happen here first,
and needs to be repeated only if the token store is deleted or Garmin
invalidates the session.
"""

from __future__ import annotations

import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError

from app.config import load_config


def main() -> None:
    config = load_config()

    email = config.garmin_email or input("Garmin e-mail: ").strip()
    password = config.garmin_password or getpass("Garmin wachtwoord: ")

    garmin = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("MFA-code: ").strip(),
    )
    try:
        garmin.login(config.garmin_tokenstore)
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
        print(f"Inloggen mislukt: {exc}")
        sys.exit(1)

    print(f"Ingelogd. Tokens opgeslagen in: {config.garmin_tokenstore}")
    print("De bot kan nu draaien zonder dat hij je wachtwoord nodig heeft.")


if __name__ == "__main__":
    main()
