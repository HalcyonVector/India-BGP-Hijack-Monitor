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
