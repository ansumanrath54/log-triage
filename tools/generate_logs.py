"""Generate a synthetic application log for demos and load tests.

    python tools/generate_logs.py --lines 200000 --out sample.log

The log covers ~6 hours. Most errors are steady background noise, but one
pattern ("Cache miss storm") ramps up sharply in the final hour and one
("Schema migration lock") appears only in the final hour, so the dashboard has
a 'rising' and a 'new' cluster to show.
"""

import argparse
import random
from datetime import datetime, timedelta, timezone

STEADY = [
    ("ERROR", "payment-service", "Timeout calling bank gateway for order {n} after {ms}ms"),
    ("ERROR", "auth-service", "Invalid token for user {n} from {ip}"),
    ("WARN", "api-gateway", "Slow response {ms}ms for GET /api/orders/{n}"),
    ("WARN", "db-pool", "Connection pool at {pct}% capacity on node {n}"),
    ("ERROR", "inventory", "NullPointerException in StockService.reserve(item={n})"),
    ("INFO", "api-gateway", "Request {n} completed in {ms}ms"),
    ("WARN", "worker", "Retrying job {uuid} attempt {n}"),
    ("ERROR", "storage", "Cannot write file '/var/data/export_{n}.csv': disk quota exceeded"),
]
RISING = ("ERROR", "cache", "Cache miss storm for key user:{n} backend latency {ms}ms")
NEW_AT_END = ("FATAL", "migrator", "Schema migration lock held by pid {n} for {ms}ms")


def fill(template: str, rng: random.Random) -> str:
    return template.format(
        n=rng.randint(1, 99999),
        ms=rng.randint(20, 9000),
        pct=rng.randint(70, 100),
        ip=f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        uuid="%08x-%04x-%04x-%04x-%012x"
        % (rng.getrandbits(32), rng.getrandbits(16), rng.getrandbits(16), rng.getrandbits(16), rng.getrandbits(48)),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=100_000)
    ap.add_argument("--out", default="sample.log")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    hours = 6
    end = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=hours)

    with open(args.out, "w", encoding="utf-8") as f:
        for _ in range(args.lines):
            offset = rng.random() * hours * 3600
            ts = start + timedelta(seconds=offset)
            in_last_hour = offset >= (hours - 1) * 3600
            roll = rng.random()
            if in_last_hour and roll < 0.25:
                level, svc, tpl = RISING
            elif in_last_hour and roll < 0.28:
                level, svc, tpl = NEW_AT_END
            elif offset > 3600 * 3 and roll < 0.02:
                level, svc, tpl = RISING  # tiny trickle earlier so it is 'rising', not 'new'
            else:
                level, svc, tpl = rng.choice(STEADY)
            f.write(f"{ts.strftime('%Y-%m-%dT%H:%M:%S')}Z {level} {svc} {fill(tpl, rng)}\n")
    print(f"Wrote {args.lines} lines to {args.out}")


if __name__ == "__main__":
    main()
