#!/usr/bin/env python3
"""Sign a local sample webhook using the configured tenant secret."""

import argparse
import hashlib
import hmac
import json
import os
import time
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--body-file", default="examples/webhook-message.json")
    args = parser.parse_args()
    secrets = json.loads(os.environ.get("WEBHOOK_SECRETS_JSON", "{}"))
    secret = secrets.get(args.tenant_id)
    if not isinstance(secret, str) or len(secret) < 32:
        raise SystemExit("No 32-character webhook secret configured for this tenant")
    with open(args.body_file, "rb") as body_file:
        body = body_file.read()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    print(f"X-Hermes-Timestamp: {timestamp}")
    print(f"X-Hermes-Signature: sha256={signature}")


if __name__ == "__main__":
    main()

