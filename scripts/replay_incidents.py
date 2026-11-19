"""
Replays real, documented routing incidents through the monitor's own
detection code, using RIPE RIS's raw update archives (MRT files from
data.ris.ripe.net, which go back to 1999; RIPEstat's update API only keeps
recent history).

The live monitor has never seen a real hijack of Indian space, so its recall
was only tested on fabricated fixtures. This checks the same functions
(classify_announcement, find_route_leak) against what route collectors
actually recorded during four well-known incidents. Each incident gets a
small baseline (the victim's block and its real origin, from the public
write-ups cited below) in place of the Indian baseline.

Writes docs/validation/replay.json. Needs `pip install mrtparse` (only this
script uses it). Downloads ~10 MB of public RIS files into --cache.

Usage: python scripts/replay_incidents.py [--cache DIR]
"""
import argparse
import ipaddress
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import mrtparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.detector.monitor import classify_announcement, collapse_path, find_route_leak  # noqa: E402

OUT = ROOT / "docs" / "validation" / "replay.json"
RIS = "https://data.ris.ripe.net/{rrc}/{y}.{m}/updates.{ymd}.{hm}.gz"

INCIDENTS = [
    {
        "id": "youtube-2008",
        "name": "Pakistan Telecom hijacks YouTube",
        "date": "2008-02-24",
        "kind": "hijack",
        "summary": "AS17557 announced 208.65.153.0/24, a more-specific of YouTube's /22, and took YouTube offline worldwide for about two hours.",
        "source": "https://www.ripe.net/publications/news/industry-developments/youtube-hijacking-a-ripe-ncc-ris-case-study",
        "baseline": {"208.65.152.0/22": 36561},
        "expect": {"origin": 17557},
        "collector": "rrc00",
        "files": ["20080224.1845", "20080224.1850", "20080224.1855"],
    },
    {
        "id": "route53-2018",
        "name": "eNet hijacks Amazon Route 53 DNS",
        "date": "2018-04-24",
        "kind": "hijack",
        "summary": "AS10297 announced /24s inside Amazon's Route 53 address space, redirecting DNS for a cryptocurrency wallet site to a phishing server.",
        "source": "https://blog.cloudflare.com/bgp-leaks-and-crypto-currencies/",
        "baseline": {"205.251.192.0/21": 16509},
        "expect": {"origin": 10297},
        "collector": "rrc00",
        "files": ["20180424.1100", "20180424.1105", "20180424.1110"],
    },
    {
        "id": "verizon-leak-2019",
        "name": "Verizon leaks routes optimised by DQE",
        "date": "2019-06-24",
        "kind": "leak",
        "summary": "AS396531 passed more-specifics it learned from DQE (AS33154) on to Verizon (AS701), which carried them worldwide; Cloudflare and others lost traffic for about two hours.",
        "source": "https://blog.cloudflare.com/how-verizon-and-a-bgp-optimizer-knocked-large-parts-of-the-internet-offline-today/",
        "baseline": {"104.16.0.0/12": 13335},
        "expect": {"leaker": 396531},
        "collector": "rrc00",
        "files": ["20190624.1035", "20190624.1040", "20190624.1045"],
    },
    {
        "id": "cloudflare-dns-2024",
        "name": "Eletronet hijacks Cloudflare's 1.1.1.1, and 1.1.1.0/24 leaks",
        "date": "2024-06-27",
        "kind": "hijack + leak",
        "summary": "AS267613 announced 1.1.1.1/32 inside Cloudflare's 1.1.1.0/24, and the /24 itself was passed on through AS262504, making the public DNS resolver unreachable for some networks.",
        "source": "https://blog.cloudflare.com/cloudflare-1111-incident-on-june-27-2024/",
        "baseline": {"1.1.1.0/24": 13335},
        "expect": {"leaker": 262504},
        "note": ("The /32 hijack never reached a RIS collector (no collector peer accepted a /32). "
                 "rrc15 (Sao Paulo) did record the leak, as paths like 1031 > 262504 > 267613 > 13335, "
                 "but no Tier-1 network sits on both sides of it, so the Tier-1 heuristic misses it."),
        "collector": "rrc15",
        "files": ["20240627.1850", "20240627.1855", "20240627.1900"],
    },
]


def file_url(rrc, stamp):
    ymd, hm = stamp.split(".")
    return RIS.format(rrc=rrc, y=ymd[:4], m=ymd[4:6], ymd=ymd, hm=hm)


def fetch(url, cache):
    rrc, month, name = url.rsplit("/", 3)[-3:]
    path = cache / rrc / month / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "bgp-incident-replay"})
        with urllib.request.urlopen(req, timeout=120) as r:
            path.write_bytes(r.read())
    return path


def updates(path):
    """Yield (timestamp, peer_as, as_path, announced_prefixes) from an MRT
    updates file, covering IPv4 NLRI and MP_REACH (IPv6) announcements."""
    for entry in mrtparse.Reader(str(path)):
        d = entry.data
        msg = d.get("bgp_message")
        if not msg or 2 not in (msg.get("type") or {}):
            continue
        as_path, prefixes = [], []
        for attr in msg.get("path_attributes", []):
            t = next(iter(attr["type"]))
            if t in (2, 17):  # AS_PATH / AS4_PATH
                path_hops = []
                for seg in attr["value"]:
                    vals = [int(v) for v in seg["value"]]
                    path_hops.extend(vals if 2 in seg["type"] else [vals])
                if t == 2 or not as_path:
                    as_path = path_hops
            elif t == 14:  # MP_REACH_NLRI
                for n in attr["value"].get("nlri", []):
                    prefixes.append(f"{n['prefix']}/{n['length']}")
        prefixes += [f"{n['prefix']}/{n['length']}" for n in msg.get("nlri", [])]
        if prefixes and as_path:
            ts = int(next(iter(d["timestamp"])))
            yield ts, int(d.get("peer_as", 0)), as_path, prefixes


def origin_of(path):
    last = path[-1]
    return last[0] if isinstance(last, list) else last


def replay(inc, cache):
    exact = dict(inc["baseline"])
    networks = {ipaddress.ip_network(p): a for p, a in exact.items()}
    in_scope = flagged = clean = 0
    flags, leaks, first = {}, {}, None
    for stamp in inc["files"]:
        path = fetch(file_url(inc["collector"], stamp), cache)
        for ts, peer_as, as_path, prefixes in updates(path):
            origin = origin_of(as_path)
            leak = find_route_leak(as_path)
            for p in prefixes:
                v = classify_announcement(p, origin, exact, networks)
                if v is None:
                    continue
                in_scope += 1
                if v["flagged"]:
                    flagged += 1
                    key = f"{p}|{origin}"
                    f = flags.setdefault(key, {"prefix": p, "origin": origin, "event_type": v["event_type"],
                                               "peers": set(), "first_seen": ts,
                                               "example_path": collapse_path(as_path)})
                    f["peers"].add(peer_as)
                    first = min(first or ts, ts)
                else:
                    clean += 1
                if leak:
                    leaker, sandwich = leak
                    l = leaks.setdefault(leaker, {"leaker": leaker, "prefixes": set(), "peers": set(),
                                                  "first_seen": ts, "example_sandwich": sandwich})
                    l["prefixes"].add(p)
                    l["peers"].add(peer_as)
                    first = min(first or ts, ts)

    flag_rows = sorted(({**f, "peers": len(f["peers"])} for f in flags.values()),
                       key=lambda f: -f["peers"])
    leak_rows = sorted(({**l, "prefixes": len(l["prefixes"]), "peers": len(l["peers"])} for l in leaks.values()),
                       key=lambda l: -l["prefixes"])
    if "origin" in inc["expect"]:
        caught = any(f["origin"] == inc["expect"]["origin"] for f in flag_rows)
    else:
        caught = any(l["leaker"] == inc["expect"]["leaker"] for l in leak_rows)
    return {
        **{k: inc[k] for k in ("id", "name", "date", "kind", "summary", "source", "baseline", "expect", "collector")},
        "note": inc.get("note"),
        "files": [file_url(inc["collector"], s) for s in inc["files"]],
        "window_utc": [time.strftime("%Y-%m-%dT%H:%MZ", time.strptime(s, "%Y%m%d.%H%M")) for s in inc["files"]],
        "announcements_in_scope": in_scope,
        "announcements_flagged": flagged,
        "announcements_clean": clean,
        "flags": flag_rows[:10],
        "leaks": leak_rows[:10],
        "first_detection_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(first)) if first else None,
        "caught": caught,
    }


def main():
    parser = argparse.ArgumentParser(description="Replay documented BGP incidents through the detector.")
    parser.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "ris-mrt")
    args = parser.parse_args()
    results = []
    for inc in INCIDENTS:
        r = replay(inc, args.cache)
        results.append(r)
        print(f"{'CAUGHT' if r['caught'] else 'MISSED'}  {r['id']}: {r['announcements_in_scope']} announcements "
              f"in scope, {r['announcements_flagged']} flagged, {len(r['leaks'])} leakers, first at {r['first_detection_utc']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": time.time(), "incidents": results}, indent=1, default=list) + "\n",
                   encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
