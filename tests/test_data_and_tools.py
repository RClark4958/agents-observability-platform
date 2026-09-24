import json

from payerbench.data import world
from payerbench.tools import (
    check_eligibility,
    find_providers,
    get_claim,
    get_prior_auths,
    list_claims,
    search_plan_document,
)


def test_world_is_deterministic():
    w = world()
    assert len(w.members) == 40
    first = w.members["M-SYNTH-000001"]
    # Same seed, same person, every run.
    assert first.first_name == world().members["M-SYNTH-000001"].first_name
    assert w.claims and w.prior_auths and w.providers


def test_eligibility_tool_round_trips_json():
    out = json.loads(check_eligibility.invoke({"member_id": "m-synth-000001"}))
    assert out["member_id"] == "M-SYNTH-000001"
    assert out["plan"] in {"Bronze HMO", "Silver PPO", "Gold PPO", "Medicare Advantage Choice"}
    assert "deductible" in out


def test_unknown_member_is_an_error_not_an_exception():
    out = json.loads(check_eligibility.invoke({"member_id": "M-NOPE"}))
    assert out["error"] == "member_not_found"


def test_claims_and_denial_reason():
    w = world()
    denied = next(c for c in w.claims.values() if c.status == "denied")
    listed = json.loads(list_claims.invoke({"member_id": denied.member_id}))
    assert listed["count"] >= 1
    one = json.loads(get_claim.invoke({"claim_id": denied.claim_id}))
    assert one["denial_code"] and one["denial_reason"]


def test_prior_auths_and_plan_doc_search():
    w = world()
    pa = next(iter(w.prior_auths.values()))
    out = json.loads(get_prior_auths.invoke({"member_id": pa.member_id}))
    assert out["count"] >= 1
    doc = json.loads(search_plan_document.invoke({"query": "MRI prior authorization"}))
    assert doc["results"][0]["section"] == "prior authorization"


def test_find_providers_filters_network():
    out = json.loads(find_providers.invoke({"specialty": "cardiology", "in_network_only": True}))
    assert all(p["in_network"] for p in out["providers"])
