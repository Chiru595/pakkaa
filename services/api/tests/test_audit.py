from sqlalchemy import delete, select, update

from app.models import AuditEvent
from app.security import audit


def _seed(db, n=3):
    for i in range(n):
        audit.record(db, f"test.event{i}", actor_user_id="u1", org_id="o1", payload={"i": i})
    db.commit()


def test_chain_verifies(db):
    _seed(db)
    check = audit.verify_chain(db)
    assert check.ok and check.events == 3


def test_tampered_payload_detected(db):
    _seed(db)
    db.execute(update(AuditEvent).where(AuditEvent.seq == 2).values(payload={"i": 99}))
    db.commit()
    check = audit.verify_chain(db)
    assert not check.ok and check.broken_at_seq == 2


def test_tampered_fields_detected_even_with_recomputed_payload_hash(db):
    _seed(db)
    db.execute(update(AuditEvent).where(AuditEvent.seq == 1).values(action="test.approved"))
    db.commit()
    assert audit.verify_chain(db).broken_at_seq == 1


def test_deleted_event_detected(db):
    _seed(db)
    db.execute(delete(AuditEvent).where(AuditEvent.seq == 2))
    db.commit()
    check = audit.verify_chain(db)
    assert not check.ok and check.broken_at_seq == 3


def test_login_and_org_creation_are_audited(client, login_as, db):
    s = login_as("9000000001", "Asha")
    client.post("/api/orgs", json={"name": "Acme Studio"}, headers=s["headers"])
    actions = db.execute(select(AuditEvent.action).order_by(AuditEvent.seq)).scalars().all()
    assert actions == ["user.signup", "user.consent", "auth.login", "org.create"]
    assert audit.verify_chain(db).ok
