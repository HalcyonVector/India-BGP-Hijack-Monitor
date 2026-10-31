"""
Tracked Indian ASNs. Verified live via RIPEstat as-overview on 2026-07-11 --
all currently announced. BGP-level tracking doesn't depend on RIPE Atlas
probe hosting, so this covers Vodafone Idea even though no RIPE Atlas probe
exists for it anywhere in India (a gap in the earlier route-mapper project).
"""

TRACKED_ASNS = {
    24560: "Bharti Airtel Ltd",
    9498: "Bharti Airtel Ltd (secondary block)",
    55836: "Reliance Jio Infocomm Ltd",
    9829: "BSNL / National Internet Backbone",
    24309: "ACT Fibernet (Atria Convergence Technologies)",
    55410: "Vodafone Idea Ltd",
    4755: "Tata Communications Ltd (VSNL)",
    9583: "Sify Limited",
    24186: "RailTel Corporation of India Ltd",
}

# ASNs run by the same company. One announcing the other's space is internal
# traffic engineering, not a hijack -- found live: AS24560 originates /24s
# out of AS9498's blocks (and vice versa) all day.
SIBLING_GROUPS = [
    {24560, 9498},  # Bharti Airtel
]


def same_org(a, b):
    return a == b or any(a in g and b in g for g in SIBLING_GROUPS)
