"""
Import cofounder's gauge_readings.csv into the PET Lab Monitor API.

Usage (run on any machine that can reach the API):
    python scripts/import_gauge_csv.py gauge_readings.csv

The script logs in, uploads the CSV to POST /api/gauges/import-csv, and
prints how many rows were inserted plus any row-level errors.
"""
import sys
import pathlib
import requests

API = "http://192.168.4.46:8000"


def main():
    import os
    username = os.environ.get("PETLAB_USER")
    password = os.environ.get("PETLAB_PASS")
    if not username or not password:
        print("Set PETLAB_USER and PETLAB_PASS environment variables (no default credentials).")
        sys.exit(1)

    csv_path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "gauge_readings.csv")
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    # Login
    r = requests.post(f"{API}/auth/login",
                      data={"username": username, "password": password})
    r.raise_for_status()
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Upload CSV
    with open(csv_path, "rb") as f:
        r = requests.post(f"{API}/api/gauges/import-csv",
                          headers=headers,
                          files={"file": ("gauge_readings.csv", f, "text/csv")})
    r.raise_for_status()
    result = r.json()
    print(f"Inserted: {result['inserted']} rows")
    if result["errors"]:
        print(f"Errors ({len(result['errors'])}):")
        for e in result["errors"]:
            print(f"  {e}")
    else:
        print("No errors.")


if __name__ == "__main__":
    main()
