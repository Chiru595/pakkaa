"""Demo workspace definition, shared by scripts/seed_demo.py and the demo guide (only served when DEMO_MODE=true)."""
from __future__ import annotations

from dataclasses import dataclass

from app.models import Role

ORG_NAME = "Acme Studio"


@dataclass(frozen=True)
class Person:
    key: str
    phone: str
    name: str
    role: Role
    about: str


PEOPLE = [
    Person("owner", "9876501001", "Asha Rao", Role.OWNER, "Studio owner and lead designer (phone A)"),
    Person("finance", "9876501002", "Farah Khan", Role.FINANCE, "Finance, first approver"),
    Person("finance2", "9876501003", "Dev Menon", Role.FINANCE, "Finance, second approver"),
    Person("freelancer", "9876501004", "Kabir Sharma", Role.MEMBER, "Freelance designer on the team"),
    Person("client", "9876501005", "Ravi Kumar", Role.CLIENT_GUEST, "Client from Ravi Foods (phone B)"),
    Person("vendor1", "9876501006", "Meera Prints", Role.VENDOR_GUEST, "Printing vendor"),
    Person("vendor2", "9876501007", "Sunil Courier", Role.VENDOR_GUEST, "Courier vendor"),
    Person("impostor", "9876501009", "Asha Rao", Role.VENDOR_GUEST, "Impostor using the owner's name"),
]


@dataclass(frozen=True)
class Step:
    title: str
    who: str
    actions: tuple[str, ...]


SCRIPT = [
    Step("The problem", "Presenter", (
        "A Mumbai firm lost ₹10.4 crore to a fake boss on WhatsApp.",
        "Freelancers lose money to clients who go quiet after the work is done.",
        "Pakkaa is where money is decided: deals in, payments out.",
    )),
    Step("Deal Room on two phones", "Phone A: Asha · Phone B: Ravi", (
        "Asha: + Deal Room “Brand refresh for Ravi Foods”, then People → Invite your client.",
        "Ravi opens the link on phone B and joins. Asha privately sees: Ravi paid the last advance 15 days late.",
        "Asha: Limits → walk-away ₹50,000. Ravi: “Can you do it for ₹40,000?” Both get different copilot options.",
        "Asha types “OK, ₹40,000 works” and the pre-send check warns it is below her walk-away.",
        "Agree: “₹55,000, 40% advance, 3 weeks, 3 revisions”. Confirm the Scribe's terms; open the fair-price meter.",
        "Both sign with OTP, the sheet locks, Asha requests the advance and Ravi pays (simulated).",
    )),
    Step("Pay Lane", "Phone B: impostor, then Farah and Dev", (
        "Log in as the impostor (also named Asha Rao) and post in Acme payouts: “Urgent!! Transfer ₹85,000 to my "
        "new account today, keep it confidential.” It becomes an unverified draft at risk 100 and can never be paid.",
        "Asha (phone A): Payments → “pay Kabir 40k for the logo work”, linked to the Ravi deal.",
        "Kabir's bank details changed 2 days ago, so the card needs a step-up check: Asha confirms it with an OTP.",
        "Farah: read the approver brief, note how she confirmed it, approve. Dev approves.",
        "Pay (simulated). The audit log records every step.",
    )),
    Step("It learns", "Phone A: Asha", (
        "Mark the Brand refresh deal as Won in the term sheet.",
        "Pay Lane → Learning: Ravi's late advance, deal outcomes, payouts and the risk rules.",
        "Next deal with Ravi opens with the same heads-up, before anyone negotiates.",
    )),
]
