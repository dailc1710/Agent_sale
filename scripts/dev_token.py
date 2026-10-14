#!/usr/bin/env python3
"""Mint a short-lived local JWT for development only."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--role", choices=("tenant_admin", "sales_agent", "reviewer", "viewer"), required=True)
    parser.add_argument("--customer-id", action="append", default=[])
    parser.add_argument("--subject", default="local-dev-user")
    args = parser.parse_args()
    if os.environ.get("APP_ENV") != "development":
        sys.exit("Refusing to mint a token unless APP_ENV=development")
    secret = os.environ.get("JWT_SECRET", "")
    if len(secret) < 32:
        sys.exit("Set JWT_SECRET to at least 32 characters")
    now = datetime.now(timezone.utc)
    print(jwt.encode({
        "sub": args.subject,
        "tenant_id": args.tenant_id,
        "role": args.role,
        "customer_ids": args.customer_id,
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }, secret, algorithm="HS256"))


if __name__ == "__main__":
    main()

