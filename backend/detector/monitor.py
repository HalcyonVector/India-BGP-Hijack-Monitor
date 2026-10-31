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
import queue
import sys
import threading
import time
from pathlib import Path

import requests
import websocket

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from backend.detector.targets import TRACKED_ASNS, same_org
from db import store

# Real paths re-announce the same route constantly; one event per
# (prefix, origin) per window is the signal, the rest is noise.
EVENT_DEDUPE_SECONDS = 6 * 3600

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
    {prefix_str: asn} exact map, plus {network: asn} for containment checks
    against baseline (less-specific) blocks.
    """
    rows = conn.execute("SELECT asn, prefix FROM baseline_prefixes").fetchall()
    exact = {}
    networks = {}
    for row in rows:
        exact[row["prefix"]] = row["asn"]
        try:
            networks[ipaddress.ip_network(row["prefix"], strict=False)] = row["asn"]
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
    # Walk up the supernets, most specific first: at most 32 (v4) / 128 (v6)
    # dict lookups, instead of scanning all ~21k baseline networks. A single
    # RIS message can carry hundreds of prefixes, so this matters on a free
    # tier CPU.
    for plen in range(net.prefixlen - 1, -1, -1):
        asn = networks.get(net.supernet(new_prefix=plen))
        if asn is not None:
            return asn, "covering"
    return None, None


def classify_announcement(prefix_str, origin, exact, networks):
    """
    The detection decision itself, kept pure so the live monitor and the
    dashboard's "try the detector" endpoint (/api/check) run the exact same
    logic. Returns None for a prefix outside every tracked baseline.
    """
    expected_asn, match_kind = find_expected_asn(prefix_str, exact, networks)
    if expected_asn is None:
        return None
    flagged = not same_org(origin, expected_asn)
    event_type = None
    if flagged:
        event_type = "unexpected_more_specific" if match_kind == "covering" else "origin_mismatch"
    return {"expected_asn": expected_asn, "match_kind": match_kind,
            "flagged": flagged, "event_type": event_type}


def is_rpki_invalid(rpki_status):
    """RIPEstat never returns a bare 'invalid' -- it says *why*:
    'invalid_asn' (wrong origin) or 'invalid_length' (too specific for the
    ROA's maxLength). An exact == 'invalid' check silently never fired."""
    return bool(rpki_status) and rpki_status.startswith("invalid")


def severity_for(rpki_status):
    """
    A different origin is not automatically a hijack. In real traffic most
    of them are customers announcing a sub-block of their ISP's address
    space (provider-assigned delegation), and the ISP has published a ROA
    authorizing exactly that. So:
      invalid -> critical  (a ROA exists and says this origin is NOT allowed;
                            RIPEstat spells it 'invalid_asn' / 'invalid_length')
      valid   -> info      (a ROA explicitly authorizes this origin)
      other   -> warning   (no ROA either way -- can't tell, worth a look)
    """
    if is_rpki_invalid(rpki_status):
        return "critical"
    if rpki_status == "valid":
        return "info"
    return "warning"


def collapse_path(path):
    """Drops AS-path prepending (e.g. [3356, 9498, 9498, 9498] -> [3356, 9498])."""
    out = []
    for hop in path or []:
        if not out or out[-1] != hop:
            out.append(hop)
    return out


def upstream_from_path(path):
    """The hop right before the origin, i.e. who handed this route to the
    rest of the internet. None for a direct peering or an AS_SET."""
    hops = collapse_path(path)
    if len(hops) < 2 or isinstance(hops[-2], list) or isinstance(hops[-1], list):
        return None
    return hops[-2]


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


def start_name_resolver():
    """Resolves holder names for newly seen upstream ASNs off the message
    path, so a slow RIPEstat call never stalls the WebSocket handler."""
    pending = queue.Queue()

    def worker():
        conn = store.get_connection()
        while True:
            asn = pending.get()
            if store.has_asn_name(conn, asn):
                continue
            store.set_asn_name(conn, asn, lookup_asn_org(asn))
            conn.commit()
            time.sleep(0.5)  # be polite to the free API

    threading.Thread(target=worker, daemon=True).start()
    return pending


def record_normal_activity(conn, data, exact, networks, name_queue):
    """Records the (non-hijack) traffic for tracked prefixes: per-minute
    counts, a short rolling feed, and observed upstreams."""
    ts = data.get("timestamp") or time.time()
    path = data.get("path")
    origin = origin_asn_from_path(path)
    upstream = upstream_from_path(path)

    announced = [p for a in (data.get("announcements") or []) for p in a.get("prefixes", [])]
    withdrawn = data.get("withdrawals") or []

    for kind, prefixes in (("announce", announced), ("withdraw", withdrawn)):
        per_asn = {}
        for prefix_str in prefixes:
            asn, _ = find_expected_asn(prefix_str, exact, networks)
            if asn is not None:
                per_asn.setdefault(asn, []).append(prefix_str)
        for asn, matched in per_asn.items():
            store.record_activity(conn, asn, ts,
                                  announcements=len(matched) if kind == "announce" else 0,
                                  withdrawals=len(matched) if kind == "withdraw" else 0)
            store.insert_recent_update(
                conn, timestamp=ts, kind=kind, prefix=matched[0], prefix_count=len(matched),
                asn=asn, origin_asn=origin if kind == "announce" else None,
                as_path=json.dumps(collapse_path(path)) if kind == "announce" else None,
                collector=data.get("host"), peer_asn=_int_or_none(data.get("peer_asn")),
            )
            if kind == "announce" and upstream and same_org(origin, asn):
                if store.record_upstream(conn, asn, upstream, ts):
                    name_queue.put(upstream)


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def run_monitor(max_messages=None, max_seconds=None):
    store.init_db()
    conn = store.get_connection()
    exact, networks = build_expected_lookup(conn)
    targets = subscription_targets(conn)
    name_queue = start_name_resolver()
    print(f"Subscribing to {len(targets)} representative prefixes across "
          f"{len(TRACKED_ASNS)} tracked ASNs...")

    state = {"count": 0, "events": 0, "start": time.monotonic()}
    last_flagged = {}  # (prefix, origin) -> wall-clock time it was last stored

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
        record_normal_activity(conn, data, exact, networks, name_queue)
        state["count"] += 1
        if state["count"] % 200 == 0:
            store.prune_activity(conn)
        conn.commit()  # commit every message, not just ones that flag an event

        origin = origin_asn_from_path(data.get("path"))
        announced = [p for a in (data.get("announcements") or []) for p in a.get("prefixes", [])]
        for prefix_str in announced:
            verdict = classify_announcement(prefix_str, origin, exact, networks)
            if verdict is None or not verdict["flagged"]:
                continue  # not one of ours, or matches expected owner (or a sibling) -- normal

            key = (prefix_str, origin)
            if time.time() - last_flagged.get(key, 0) < EVENT_DEDUPE_SECONDS:
                continue
            last_flagged[key] = time.time()

            expected_asn, event_type = verdict["expected_asn"], verdict["event_type"]
            rpki_status = check_rpki(origin, prefix_str) if origin else None
            observed_org = lookup_asn_org(origin) if origin else None
            severity = severity_for(rpki_status)

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

    def on_error(ws, error):
        # websocket-client otherwise swallows exceptions raised in
        # on_message -- which once hid a KeyError that silently skipped
        # every announcement. Make any such failure loud.
        print(f"  [MONITOR ERROR] {type(error).__name__}: {error}")

    ws = websocket.WebSocketApp(RIS_WS_URL, on_open=on_open, on_message=on_message,
                                on_error=on_error)
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
