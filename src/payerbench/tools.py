"""Tools the member-services agent can call. Each returns JSON-serializable dicts.

Tool results are what the model sees, so they are also where indirect prompt injection would
arrive in the red-team phase; keep them structured.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

from langchain_core.tools import tool

from payerbench.data import PLAN_DOCUMENT, world


def _dumps(obj: object) -> str:
    def default(o: object) -> str:
        if isinstance(o, date):
            return o.isoformat()
        raise TypeError(type(o))

    return json.dumps(obj, default=default)


@tool
def check_eligibility(member_id: str) -> str:
    """Look up whether a member is currently covered and on which plan.

    Returns plan name, active flag, effective/termination dates, PCP, and deductible progress.
    """
    m = world().members.get(member_id.strip().upper())
    if not m:
        return _dumps({"error": "member_not_found", "member_id": member_id})
    return _dumps(
        {
            "member_id": m.member_id,
            "name": f"{m.first_name} {m.last_name}",
            "dob": m.dob,
            "plan": m.plan,
            "active": m.active,
            "effective_date": m.effective_date,
            "term_date": m.term_date,
            "pcp": m.pcp,
            "deductible": {"total": m.deductible_total, "met": m.deductible_met},
            "out_of_pocket": {"max": m.oop_max, "met": m.oop_met},
        }
    )


@tool
def list_claims(member_id: str) -> str:
    """List all claims for a member with status, amounts, and denial code if denied."""
    rows = [asdict(c) for c in world().claims.values() if c.member_id == member_id.strip().upper()]
    rows.sort(key=lambda r: r["service_date"], reverse=True)
    return _dumps({"member_id": member_id, "count": len(rows), "claims": rows})


@tool
def get_claim(claim_id: str) -> str:
    """Get one claim by claim ID, including the plain-English meaning of any denial code."""
    from payerbench.data import DENIAL_REASONS

    c = world().claims.get(claim_id.strip().upper())
    if not c:
        return _dumps({"error": "claim_not_found", "claim_id": claim_id})
    row = asdict(c)
    if c.denial_code:
        row["denial_reason"] = DENIAL_REASONS.get(c.denial_code, "Unknown")
    return _dumps(row)


@tool
def get_prior_auths(member_id: str) -> str:
    """List prior authorization requests for a member with status, dates, and reviewer notes."""
    rows = [
        asdict(p) for p in world().prior_auths.values() if p.member_id == member_id.strip().upper()
    ]
    return _dumps({"member_id": member_id, "count": len(rows), "prior_auths": rows})


@tool
def search_plan_document(query: str) -> str:
    """Search the plan's benefits document. Use for coverage rules, limits, appeals, and network
    questions. Returns the most relevant sections."""
    q = query.lower()
    scored = []
    for title, text in PLAN_DOCUMENT.items():
        hay = f"{title} {text}".lower()
        score = sum(hay.count(w) for w in q.split() if len(w) > 2)
        if score:
            scored.append((score, title, text))
    scored.sort(reverse=True)
    top = [{"section": t, "text": x} for _, t, x in scored[:3]]
    return _dumps({"query": query, "results": top})


@tool
def find_providers(
    specialty: str, zip_code: str | None = None, in_network_only: bool = True
) -> str:
    """Find providers by specialty (cardiology, dermatology, orthopedics, physical therapy,
    endocrinology), optionally filtered by ZIP code and network status."""
    rows = [
        asdict(p)
        for p in world().providers
        if p.specialty == specialty.lower().strip()
        and (zip_code is None or p.zip_code == zip_code)
        and (not in_network_only or p.in_network)
    ]
    return _dumps({"specialty": specialty, "count": len(rows), "providers": rows[:10]})


ALL_TOOLS = [
    check_eligibility,
    list_claims,
    get_claim,
    get_prior_auths,
    search_plan_document,
    find_providers,
]
