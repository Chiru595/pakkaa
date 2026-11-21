"""Fair-price meter: places an offer against this workspace's own past deals first, then the rate-band pack."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.terms import values
from app.money import format_inr
from app.rules import load_pack


@dataclass
class Comparison:
    label: str
    median_paise: int
    count: int


@dataclass
class FairPrice:
    category: str | None
    category_label: str | None
    offer_paise: int | None
    band: dict[str, int] | None
    position: str | None
    verdict: str
    comparisons: list[Comparison] = field(default_factory=list)
    source: str = ""
    as_of: str = ""
    disclaimer: str = ""


def detect_category(*texts: str | None) -> str | None:
    pack = load_pack("rate_bands_in")
    blob = " ".join(t for t in texts if t).lower()
    best, best_len = None, 0
    for key, cat in pack["categories"].items():
        for kw in cat["keywords"]:
            if kw in blob and len(kw) > best_len:
                best, best_len = key, len(kw)
    return best


def _position(offer: int, band: dict[str, int]) -> str:
    if offer < band["p25"]:
        return "below_range"
    if offer < band["p50"]:
        return "lower_half"
    if offer <= band["p75"]:
        return "upper_half"
    return "above_range"


VERDICTS = {
    "below_range": "Below the usual range",
    "lower_half": "Within range, lower half",
    "upper_half": "Within range, upper half",
    "above_range": "Above the usual range",
}


def assess(db: Session, *, org_id: str, room_id: str, category: str | None, offer_paise: int | None,
           client_user_id: str | None) -> FairPrice:
    from app.models import Room, RoomMember, TermSheet, TermSheetStatus

    pack = load_pack("rate_bands_in")
    cat = pack["categories"].get(category) if category else None
    band = {k: int(cat[k]) * 100 for k in ("p25", "p50", "p75")} if cat else None
    result = FairPrice(category=category, category_label=cat["label"] if cat else None, offer_paise=offer_paise,
                       band=band, position=None, verdict="Add a price to compare", source=str(pack["source"]),
                       as_of=str(pack["as_of"]), disclaimer=str(pack["disclaimer"]))

    locked = db.execute(
        select(TermSheet, Room).join(Room, Room.id == TermSheet.room_id)
        .where(Room.org_id == org_id, Room.id != room_id, TermSheet.status == TermSheetStatus.LOCKED)
    ).all()
    same_cat = [values(ts.terms).get("price_paise") for ts, _ in locked if ts.category == category]
    same_cat = [int(p) for p in same_cat if p]
    if same_cat:
        result.comparisons.append(Comparison(f"Your past {cat['label'].lower() if cat else 'similar'} deals",
                                             int(median(same_cat)), len(same_cat)))
    if client_user_id:
        client_rooms = {r.room_id for r in db.execute(
            select(RoomMember).where(RoomMember.user_id == client_user_id, RoomMember.side == "client")).scalars()}
        with_client = [int(values(ts.terms)["price_paise"]) for ts, room in locked
                       if room.id in client_rooms and values(ts.terms).get("price_paise")]
        if with_client:
            result.comparisons.append(Comparison("This client's past deals with you", int(median(with_client)),
                                                 len(with_client)))
    if offer_paise and band:
        result.position = _position(offer_paise, band)
        result.verdict = VERDICTS[result.position]
    elif offer_paise:
        result.verdict = f"{format_inr(offer_paise)} offered; pick a category to compare"
    return result
