"""Validate required auth configuration without printing any secret."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret) < 32 or secret.startswith("replace-with"):
        print(
            "JWT_SECRET_KEY is missing, still a placeholder, or shorter than 32 characters. Set a strong random value in backend/.env before starting ResolveAI.",
            file=sys.stderr,
        )
        return 1
    print("ResolveAI authentication configuration is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
