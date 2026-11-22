from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from backend.app.models import PaymentWebhookEvent
from backend.app.payment_service import (
    claim_webhook_processing,
    mark_webhook_processed,
    record_verified_webhook,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PaymentWebhookEvent.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_event(db):
    event = record_verified_webhook(
        db,
        provider="razorpay",
        provider_event_id="evt_claim_test",
        event_type="payment.captured",
        payload_hash="a" * 64,
        signature_verified=True,
        metadata={"event": "payment.captured"},
    )
    db.commit()
    return event


def test_webhook_claim_is_exclusive_and_terminal_events_cannot_be_reclaimed(db):
    event = make_event(db)

    assert claim_webhook_processing(db, event.id) is True
    db.commit()

    # A second worker cannot acquire a live processing lease.
    assert claim_webhook_processing(db, event.id) is False
    db.commit()

    claimed = db.get(PaymentWebhookEvent, event.id)
    assert claimed.processing_status == "processing"
    assert claimed.processing_attempts == 1

    mark_webhook_processed(db, event.id)
    db.commit()

    assert claim_webhook_processing(db, event.id) is False
    db.commit()


def test_stale_webhook_claim_can_be_reclaimed(db):
    event = make_event(db)

    assert claim_webhook_processing(db, event.id) is True
    db.commit()

    db.execute(
        update(PaymentWebhookEvent)
        .where(PaymentWebhookEvent.id == event.id)
        .values(processing_started_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=6))
    )
    db.commit()

    assert claim_webhook_processing(db, event.id) is True
    db.commit()

    claimed = db.get(PaymentWebhookEvent, event.id)
    assert claimed.processing_status == "processing"
    assert claimed.processing_attempts == 2


def test_failed_webhook_can_be_retried_and_failure_clears_lease(db):
    event = make_event(db)

    assert claim_webhook_processing(db, event.id) is True
    db.commit()

    mark_webhook_processed(
        db,
        event.id,
        error_code="temporary_provider_error",
    )
    db.commit()

    failed = db.get(PaymentWebhookEvent, event.id)
    assert failed.processing_status == "failed"
    assert failed.processing_started_at is None
    assert failed.error_code == "temporary_provider_error"

    assert claim_webhook_processing(db, event.id) is True
    db.commit()

    retried = db.get(PaymentWebhookEvent, event.id)
    assert retried.processing_status == "processing"
    assert retried.processing_attempts == 2
    assert retried.error_code is None


def test_duplicate_webhook_insert_is_idempotent_and_conflicts_are_rejected(db):
    first = make_event(db)

    duplicate = record_verified_webhook(
        db,
        provider="razorpay",
        provider_event_id="evt_claim_test",
        event_type="payment.captured",
        payload_hash="a" * 64,
        signature_verified=True,
    )
    assert duplicate.id == first.id

    with pytest.raises(ValueError, match="conflicting content"):
        record_verified_webhook(
            db,
            provider="razorpay",
            provider_event_id="evt_claim_test",
            event_type="payment.failed",
            payload_hash="b" * 64,
            signature_verified=True,
        )


def test_unverified_webhook_cannot_be_claimed_or_persisted(db):
    with pytest.raises(PermissionError):
        record_verified_webhook(
            db,
            provider="razorpay",
            provider_event_id="evt_untrusted",
            event_type="payment.captured",
            payload_hash="c" * 64,
            signature_verified=False,
        )
