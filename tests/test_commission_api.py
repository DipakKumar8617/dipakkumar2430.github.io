from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app import admin, channel_partners
from backend.app.models import (
    CommissionLedgerEntry,
    CommissionRule,
    StaffAuditLog,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = [
        CommissionRule.__table__,
        CommissionLedgerEntry.__table__,
        StaffAuditLog.__table__,
    ]
    CommissionRule.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def administrator():
    return SimpleNamespace(id=7)


def make_entry(
    db,
    *,
    cp_id,
    source_id,
    entry_type="accrual",
    amount="10.00",
    status="pending",
    original_id=None,
):
    entry = CommissionLedgerEntry(
        channel_partner_account_id=cp_id,
        transaction_id=100 + source_id,
        payment_attempt_id=200 + source_id,
        source_payment_ledger_entry_id=source_id,
        original_accrual_id=original_id,
        commission_rule_id=None,
        entry_type=entry_type,
        basis_amount=Decimal("100.00"),
        amount=Decimal(amount),
        rate_bps=1000,
        currency="INR",
        approval_status=status,
    )
    db.add(entry)
    db.flush()
    return entry


def test_admin_can_create_effective_dated_commission_rule(
    db, administrator
):
    payload = admin.CommissionRuleCreate(
        rate_bps=750,
        currency="inr",
        starts_at=datetime(2026, 12, 1, 0, 0),
        ends_at=datetime(2027, 1, 1, 0, 0),
    )

    result = admin.create_commission_rule(
        payload,
        SimpleNamespace(),
        administrator,
        db,
    )

    assert result["rate_bps"] == 750
    assert result["currency"] == "INR"
    assert result["created_by_account_id"] == administrator.id
    assert db.scalar(
        select(StaffAuditLog).where(
            StaffAuditLog.action == "commission_rule_created"
        )
    ) is not None


def test_overlapping_active_commission_rule_is_rejected(
    db, administrator
):
    first = admin.CommissionRuleCreate(
        rate_bps=500,
        currency="INR",
        starts_at=datetime(2026, 12, 1),
        ends_at=datetime(2027, 1, 1),
    )
    admin.create_commission_rule(
        first, SimpleNamespace(), administrator, db
    )

    overlapping = admin.CommissionRuleCreate(
        rate_bps=800,
        currency="INR",
        starts_at=datetime(2026, 12, 15),
        ends_at=datetime(2027, 2, 1),
    )

    with pytest.raises(HTTPException) as exc:
        admin.create_commission_rule(
            overlapping, SimpleNamespace(), administrator, db
        )

    assert exc.value.status_code == 409


def test_admin_review_updates_accrual_and_audit_log(
    db, administrator
):
    accrual = make_entry(db, cp_id=30, source_id=301)
    db.commit()

    payload = admin.CommissionReviewRequest(
        decision="approved",
        note="Verified against booking capture",
    )
    result = admin.review_commission_entry(
        accrual.id, payload, administrator, db
    )

    assert result["approval_status"] == "approved"
    assert result["reviewed_by_account_id"] == administrator.id
    assert result["review_note"] == "Verified against booking capture"

    audit = db.scalar(
        select(StaffAuditLog).where(
            StaffAuditLog.action == "commission_approved"
        )
    )
    assert audit is not None
    assert audit.target_account_id == 30


def test_admin_review_propagates_to_existing_reversals(
    db, administrator
):
    accrual = make_entry(db, cp_id=30, source_id=401)
    reversal = make_entry(
        db,
        cp_id=30,
        source_id=402,
        entry_type="reversal",
        amount="2.50",
        original_id=accrual.id,
    )
    db.commit()

    result = admin.review_commission_entry(
        accrual.id,
        admin.CommissionReviewRequest(decision="rejected", note="Incorrect booking"),
        administrator,
        db,
    )

    db.refresh(reversal)
    assert result["approval_status"] == "rejected"
    assert reversal.approval_status == "rejected"


def test_channel_partner_earnings_are_scoped_and_net_refunds(
    db, monkeypatch
):
    own_accrual = make_entry(
        db, cp_id=30, source_id=501, amount="10.00", status="approved"
    )
    make_entry(
        db,
        cp_id=30,
        source_id=502,
        entry_type="reversal",
        amount="2.50",
        status="approved",
        original_id=own_accrual.id,
    )
    make_entry(
        db, cp_id=31, source_id=503, amount="99.00", status="approved"
    )
    db.commit()

    monkeypatch.setattr(
        channel_partners,
        "_require_channel_partner",
        lambda request, session: SimpleNamespace(id=30),
    )

    result = channel_partners.channel_partner_earnings(
        SimpleNamespace(), db
    )

    assert len(result["items"]) == 2
    assert all(
        item["transaction_id"] != 603 for item in result["items"]
    )
    totals = result["totals_by_currency"]["INR"]
    assert totals["gross_accrued"] == "10.00"
    assert totals["total_reversed"] == "2.50"
    assert totals["net_earnings"] == "7.50"
    assert totals["approved"] == "7.50"
