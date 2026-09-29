"""One-off: backfill refunds.reason from the legacy support-ticket export (June 2026)."""

import csv
import os
import sys

from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with open(sys.argv[1]) as f, engine.begin() as conn:
    for row in csv.DictReader(f):
        conn.execute(
            text("UPDATE refunds SET reason = :reason WHERE id = :id AND reason IS NULL"),
            {"reason": row["reason"], "id": row["refund_id"]},
        )
        print("updated", row["refund_id"])
