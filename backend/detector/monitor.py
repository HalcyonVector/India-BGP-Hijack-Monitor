"""
Real-time BGP hijack/leak detector for tracked Indian ASNs.

Subscribes to RIPE RIS Live (free, public WebSocket firehose of BGP updates
seen by RIPE's route collectors worldwide) filtered by PREFIX, not by path.
This matters: filtering by "my ASN appears in the path" would miss a real
hijack, since a hijacker announcing my prefix has a path that does NOT
contain my ASN at all. Filtering by prefix (with moreSpecific=true) catches
any announcement of my address space regardless of who originates it.

Subscribing to every individual announced prefix (thousands per ASN) isn't
practical over one connection, so this subscribes to the largest ~8 blocks
per tracked ASN (db.store.top_prefixes_by_asn) -- a scoping trade-off, not
full coverage. See docs/limitations.md.
"""
import ipaddress
import json
import sys
import time
from pathlib import Path

import requests
import websocket

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from backend.detector.targets import TRACKED_ASNS
from db import store

RIS_WS_URL = "wss://ris-live.ripe.net/v1/ws/?client=cce-bgp-hijack-monitor"
RPKI_URL = "https://stat.ripe.net/data/rpki-validation/data.json"
AS_OVERVIEW_URL = "https://stat.ripe.net/data/as-overview/data.json"


def origin_asn_from_path(path):
    if not path:
        return None
    last = path[-1]
    if isinstance(last, list):  # AS_SET at origin
        return last[0] if last else None
    return last


def build_expected_lookup(conn):
    """
    {prefix_str: asn} exact map, plus a parsed list of (network, asn) for
    containment checks against baseline (less-specific) blocks.
    """
    rows = conn.execute("SELECT asn, prefix FROM baseline_prefixes").fetchall()
    exact = {}
    networks = []
    for row in rows:
        exact[row["prefix"]] = row["asn"]
        try:
            networks.append((ipaddress.ip_network(row["prefix"], strict=False), row["asn"]))
        except ValueError:
            continue
    return exact, networks


def find_expected_asn(prefix_str, exact, networks):
    if prefix_str in exact:
        return exact[prefix_str], "exact"
    try:
        net = ipaddress.ip_network(prefix_str, strict=False)
    except ValueError:
        return None, None
    # find the most specific covering baseline network
    best = None
    for base_net, asn in networks:
        if base_net.version == net.version and net.subnet_of(base_net):
            if best is None or base_net.prefixlen > best[0].prefixlen:
                best = (base_net, asn)
    if best:
        return best[1], "covering"
    return None, None


def check_rpki(asn, prefix):
    try:
        resp = requests.get(RPKI_URL, params={"resource": asn, "prefix": prefix}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", {}).get("status")
    except requests.RequestException:
        return None


def lookup_asn_org(asn):
    """
    Holder name for an ASN, e.g. 'VIL-AS-AP - Vodafone Idea Ltd'. Only
    called when an event is actually flagged (rare), so one extra RIPEstat
    call per event is negligible -- this is what turns 'observed AS99999'
    into something a human can judge as plausible or not.
    """
    try:
        resp = requests.get(AS_OVERVIEW_URL, params={"resource": f"AS{asn}"}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", {}).get("holder")
    except requests.RequestException:
        return None


def subscription_targets(conn, n_per_asn=8):
    targets = []
    for asn in TRACKED_ASNS:
        for prefix in store.top_prefixes_by_asn(conn, asn, n=n_per_asn):
            targets.append(prefix)
    return targets


def run_monitor(max_messages=None, max_seconds=None):
    store.init_db()
    conn = store.get_connection()
    exact, networks = build_expected_lookup(conn)
    targets = subscription_targets(conn)
    print(f"Subscribing to {len(targets)} representative prefixes across "
          f"{len(TRACKED_ASNS)} tracked ASNs...")

    state = {"count": 0, "events": 0, "start": time.monotonic()}

    def on_open(ws):
        for prefix in targets:
            ws.send(json.dumps({
                "type": "ris_subscribe",
                "data": {"prefix": prefix, "moreSpecific": True, "type": "UPDATE"},
            }))
        print("Subscribed. Listening...")

    def on_message(ws, message):
        d = json.loads(message)
        if d.get("type") != "ris_message":
            return
        data = d["data"]
        store.touch_monitor_status(conn)
        conn.commit()  # commit every message, not just ones that flag an event
        state["count"] += 1

        origin = origin_asn_from_path(data.get("path"))
        for prefix_str in (data.get("announcements") and
                            [a["prefix"] for a in data["announcements"]] or []):
            expected_asn, match_kind = find_expected_asn(prefix_str, exact, networks)
            if expected_asn is None or origin == expected_asn:
                continue  # not one of ours, or matches expected owner -- normal

            event_type = "unexpected_more_specific" if match_kind == "covering" else "origin_mismatch"
            rpki_status = check_rpki(origin, prefix_str) if origin else None
            observed_org = lookup_asn_org(origin) if origin else None
            severity = "critical" if rpki_status == "invalid" else "warning"

            store.insert_event(
                conn, event_type=event_type, prefix=prefix_str,
                expected_asn=expected_asn, observed_origin_asn=origin,
                observed_origin_org=observed_org,
                as_path=json.dumps(data.get("path")), rpki_status=rpki_status,
                peer=data.get("peer"), raw_message=json.dumps(data),
                severity=severity, timestamp=data.get("timestamp"),
            )
            conn.commit()
            state["events"] += 1
            print(f"  [{severity.upper()}] {prefix_str}: expected AS{expected_asn}, "
                  f"observed AS{origin} ({observed_org}) (rpki={rpki_status})")

        if max_messages and state["count"] >= max_messages:
            ws.close()
        if max_seconds and (time.monotonic() - state["start"]) >= max_seconds:
            ws.close()

    ws = websocket.WebSocketApp(RIS_WS_URL, on_open=on_open, on_message=on_message)
    try:
        ws.run_forever(ping_interval=20)
    finally:
        conn.close()
    print(f"Processed {state['count']} messages, flagged {state['events']} events.")
    return state


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the live BGP monitor.")
    parser.add_argument("--max-seconds", type=int, default=None,
                         help="Stop after N seconds (omit to run indefinitely, "
                              "e.g. as a background service via run_monitor.bat)")
    args = parser.parse_args()
    run_monitor(max_seconds=args.max_seconds)
