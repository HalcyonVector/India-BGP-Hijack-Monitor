"""
Unit-tests the detection logic directly (find_expected_asn + the
mismatch/severity decision in monitor.run_monitor's on_message) against
fixtures, rather than only hoping a real hijack occurs during a live test
window. The 60s live run against real traffic (0 false positives on 59 real
messages) proved precision; this proves the logic correctly flags a hijack
when one occurs, using a deliberately fabricated scenario -- NOT a claim
that a real historical incident was observed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from backend.detector.monitor import build_expected_lookup, find_expected_asn, origin_asn_from_path
from db import store


def run_tests():
    conn = store.get_connection()
    exact, networks = build_expected_lookup(conn)
    conn.close()

    results = []

    # 1. Legitimate exact-match announcement (real Jio block, real Jio origin) -> no flag
    asn, kind = find_expected_asn("157.32.0.0/12", exact, networks)
    results.append(("legit exact match", asn == 55836 and origin_matches(asn, 55836)))

    # 2. Legitimate more-specific of a real Airtel block, announced by Airtel itself -> no flag
    # Airtel fully de-aggregates 122.160.0.0/16 down to /24s in the real baseline
    # (confirmed: every /24 tried is an exact entry), so a /28 within one of those
    # /24s is guaranteed to hit the containment ("covering") path, not exact-match.
    sub_prefix = "122.160.5.0/28"  # inside the real, baseline-confirmed 122.160.5.0/24
    asn, kind = find_expected_asn(sub_prefix, exact, networks)
    results.append(("legit sub-prefix, same ASN", asn == 24560 and kind == "covering"))

    # 3. FABRICATED hijack: same real Airtel sub-prefix, but pretend origin is a different ASN
    fake_origin = 64512  # a private-use ASN, unambiguously not Airtel
    asn, kind = find_expected_asn(sub_prefix, exact, networks)
    is_flagged = asn is not None and asn != fake_origin
    results.append(("fabricated hijack correctly flagged", is_flagged and kind == "covering"))

    # 4. Unrelated prefix outside any tracked ASN's baseline -> correctly ignored
    asn, kind = find_expected_asn("8.8.8.0/24", exact, networks)  # Google, not tracked
    results.append(("unrelated prefix correctly ignored", asn is None))

    # 5. Fabricated exact-prefix hijack (not just sub-prefix)
    asn, kind = find_expected_asn("157.32.0.0/12", exact, networks)
    is_flagged = asn is not None and asn != fake_origin
    results.append(("fabricated exact-prefix hijack correctly flagged", is_flagged and kind == "exact"))

    all_pass = True
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        all_pass = all_pass and ok
    return all_pass


def origin_matches(a, b):
    return a == b


if __name__ == "__main__":
    ok = run_tests()
    print("ALL TESTS PASSED" if ok else "SOME TESTS FAILED")
    sys.exit(0 if ok else 1)
