#!/bin/bash
# PiTun password reset script
# Usage: ./reset-password.sh [new_password]
# If no password given, resets to "password"
#
# Run directly on RPi4:
#   bash /path/to/reset-password.sh myNewPass
#
# Run via Docker:
#   docker exec pitun-backend bash /app/scripts/reset-password.sh myNewPass

set -e

NEW_PASS="${1:-password}"
DB_PATH="${PITUN_DB:-/app/data/pitun.db}"

if [ ! -f "$DB_PATH" ]; then
    # Try alternate path
    DB_PATH="./data/pitun.db"
fi

if [ ! -f "$DB_PATH" ]; then
    echo "Error: database not found at $DB_PATH"
    echo "Set PITUN_DB env var to the correct path"
    exit 1
fi

HASH=$(python3 -c "
import bcrypt
h = bcrypt.hashpw('${NEW_PASS}'.encode(), bcrypt.gensalt()).decode()
print(h)
")

sqlite3 "$DB_PATH" "UPDATE user SET password_hash='${HASH}' WHERE username='admin';"

echo "Admin password reset successfully."
echo "Username: admin"
echo "Password: ${NEW_PASS}"
