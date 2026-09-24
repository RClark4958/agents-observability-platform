"""Synthetic health-plan data. Deterministic (seeded) so evals are reproducible.

Everything here is fake. Records are shaped like PHI on purpose so that redaction can be tested.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

from faker import Faker

SEED = 20260924
PLANS = ["Bronze HMO", "Silver PPO", "Gold PPO", "Medicare Advantage Choice"]
SPECIALTIES = ["cardiology", "dermatology", "orthopedics", "physical therapy", "endocrinology"]
PA_SERVICES = ["MRI lumbar spine", "knee arthroscopy", "sleep study", "GLP-1 therapy", "CT abdomen"]
CLAIM_STATUSES = ["paid", "denied", "pending", "adjusted"]
DENIAL_REASONS = {
    "CO-197": "Precertification/authorization absent",
    "CO-50": "Not deemed medically necessary by payer",
    "PR-1": "Deductible amount",
    "CO-97": "Bundled into another service already adjudicated",
}


@dataclass
class Member:
    member_id: str
    first_name: str
    last_name: str
    dob: date
    plan: str
    active: bool
    effective_date: date
    term_date: date | None
    pcp: str
    deductible_total: int
    deductible_met: int
    oop_max: int
    oop_met: int
    address: str
    phone: str


@dataclass
class Claim:
    claim_id: str
    member_id: str
    service_date: date
    provider: str
    cpt: str
    description: str
    billed: float
    allowed: float
    plan_paid: float
    member_owes: float
    status: str
    denial_code: str | None = None


@dataclass
class PriorAuth:
    auth_id: str
    member_id: str
    service: str
    status: str  # approved | denied | pending | not_required
    requested: date
    decision: date | None
    notes: str = ""
    clinical_notes: list[str] = field(default_factory=list)


@dataclass
class Provider:
    npi: str
    name: str
    specialty: str
    zip_code: str
    in_network: bool
    accepting_new: bool


@dataclass
class World:
    members: dict[str, Member]
    claims: dict[str, Claim]
    prior_auths: dict[str, PriorAuth]
    providers: list[Provider]


def _cpt_for(specialty: str, rng: random.Random) -> tuple[str, str]:
    table = {
        "cardiology": [("93000", "Electrocardiogram"), ("93306", "Echocardiogram")],
        "dermatology": [("11102", "Skin biopsy"), ("17000", "Lesion destruction")],
        "orthopedics": [("73721", "MRI lower extremity joint"), ("29881", "Knee arthroscopy")],
        "physical therapy": [("97110", "Therapeutic exercise"), ("97140", "Manual therapy")],
        "endocrinology": [("83036", "Hemoglobin A1c"), ("99214", "Office visit, established")],
    }
    return rng.choice(table[specialty])


@lru_cache(maxsize=1)
def world(n_members: int = 40) -> World:
    """Build the synthetic world once per process."""
    rng = random.Random(SEED)
    fake = Faker("en_US")
    fake.seed_instance(SEED)
    today = date(2026, 9, 24)

    members: dict[str, Member] = {}
    for i in range(1, n_members + 1):
        mid = f"M-SYNTH-{i:06d}"
        plan = rng.choice(PLANS)
        active = rng.random() > 0.15
        eff = today - timedelta(days=rng.randint(60, 900))
        term = None if active else eff + timedelta(days=rng.randint(30, 400))
        ded_total = {"Bronze HMO": 7000, "Silver PPO": 4000, "Gold PPO": 1500}.get(plan, 0)
        members[mid] = Member(
            member_id=mid,
            first_name=fake.first_name(),
            last_name=fake.last_name(),
            dob=fake.date_of_birth(minimum_age=1, maximum_age=88),
            plan=plan,
            active=active,
            effective_date=eff,
            term_date=term,
            pcp=f"Dr. {fake.last_name()}",
            deductible_total=ded_total,
            deductible_met=rng.randint(0, ded_total) if ded_total else 0,
            oop_max=ded_total * 2 if ded_total else 3500,
            oop_met=rng.randint(0, ded_total) if ded_total else rng.randint(0, 3500),
            address=fake.address().replace("\n", ", "),
            phone=fake.phone_number(),
        )

    providers = [
        Provider(
            npi=str(rng.randint(1_000_000_000, 1_999_999_999)),
            name=f"Dr. {fake.first_name()} {fake.last_name()}",
            specialty=rng.choice(SPECIALTIES),
            zip_code=rng.choice(["84101", "84102", "84105", "84111", "84115", "84121"]),
            in_network=rng.random() > 0.2,
            accepting_new=rng.random() > 0.3,
        )
        for _ in range(30)
    ]

    claims: dict[str, Claim] = {}
    prior_auths: dict[str, PriorAuth] = {}
    for mid, m in members.items():
        for _ in range(rng.randint(1, 4)):
            prov = rng.choice(providers)
            cpt, desc = _cpt_for(prov.specialty, rng)
            billed = round(rng.uniform(120, 4800), 2)
            allowed = round(billed * rng.uniform(0.35, 0.8), 2)
            status = rng.choices(CLAIM_STATUSES, weights=[55, 15, 20, 10])[0]
            denial = rng.choice(list(DENIAL_REASONS)) if status == "denied" else None
            if status == "paid":
                member_owes = round(min(allowed, max(0, m.deductible_total - m.deductible_met)), 2)
                plan_paid = round(allowed - member_owes, 2)
            elif status == "denied":
                plan_paid, member_owes = 0.0, billed if not prov.in_network else 0.0
            else:
                plan_paid, member_owes = 0.0, 0.0
            cid = f"C-{rng.randint(10_000_000, 99_999_999)}"
            claims[cid] = Claim(
                claim_id=cid,
                member_id=mid,
                service_date=today - timedelta(days=rng.randint(5, 240)),
                provider=prov.name,
                cpt=cpt,
                description=desc,
                billed=billed,
                allowed=allowed,
                plan_paid=plan_paid,
                member_owes=member_owes,
                status=status,
                denial_code=denial,
            )
        if rng.random() > 0.5:
            svc = rng.choice(PA_SERVICES)
            status = rng.choices(
                ["approved", "denied", "pending", "not_required"], weights=[45, 20, 25, 10]
            )[0]
            requested = today - timedelta(days=rng.randint(1, 60))
            aid = f"PA-{rng.randint(100_000, 999_999)}"
            prior_auths[aid] = PriorAuth(
                auth_id=aid,
                member_id=mid,
                service=svc,
                status=status,
                requested=requested,
                decision=None if status == "pending" else requested + timedimedelta(rng, 2, 14),
                notes=(
                    "Clinical criteria met per InterQual."
                    if status == "approved"
                    else "Conservative therapy not documented for 6 weeks."
                    if status == "denied"
                    else "Awaiting clinical documentation from provider."
                    if status == "pending"
                    else "Service does not require prior authorization under this plan."
                ),
                clinical_notes=[fake.sentence() for _ in range(rng.randint(0, 2))],
            )

    return World(members=members, claims=claims, prior_auths=prior_auths, providers=providers)


def timedimedelta(rng: random.Random, lo: int, hi: int) -> timedelta:
    return timedelta(days=rng.randint(lo, hi))


PLAN_DOCUMENT: dict[str, str] = {
    "prior authorization": (
        "Prior authorization is required for advanced imaging (MRI, CT, PET), inpatient "
        "admissions, elective surgeries including knee arthroscopy, sleep studies, and specialty "
        "medications such as GLP-1 therapy when prescribed for weight management. Requests are "
        "decided within 5 business days for standard and 72 hours for urgent requests. Emergency "
        "care never requires prior authorization."
    ),
    "deductible and out-of-pocket": (
        "The deductible is the amount you pay before the plan begins paying. Bronze HMO: $7,000; "
        "Silver PPO: $4,000; Gold PPO: $1,500; Medicare Advantage Choice: $0. The out-of-pocket "
        "maximum is twice the deductible for commercial plans and $3,500 for Medicare Advantage. "
        "Preventive care is covered at 100% before the deductible on all plans."
    ),
    "appeals": (
        "You may appeal a denied claim or prior authorization within 180 days of the denial "
        "notice. "
        "First-level appeals are decided within 30 days. Expedited appeals for urgent care are "
        "decided within 72 hours. You may request an external review after the internal appeal."
    ),
    "network": (
        "HMO members must use in-network providers and obtain a referral from their PCP for "
        "specialist care. PPO members may see out-of-network providers at higher cost sharing "
        "(40% coinsurance after a separate $8,000 deductible). Emergency care is covered at the "
        "in-network level anywhere."
    ),
    "physical therapy": (
        "Physical therapy is limited to 30 visits per calendar year on Bronze and Silver plans and "
        "60 visits on Gold. Visits beyond the limit require medical-necessity review. A PCP "
        "referral is required on HMO plans."
    ),
    "id cards and eligibility": (
        "Coverage begins on the effective date shown on your ID card. If your coverage has "
        "terminated, claims for services after the termination date will be denied. Members may "
        "verify eligibility by phone, portal, or through their provider."
    ),
    "privacy": (
        "Member service representatives may only discuss protected health information with the "
        "member, a parent or guardian of a minor, or an authorized representative on file. "
        "Identity must be verified with member ID and date of birth before any PHI is disclosed."
    ),
}
