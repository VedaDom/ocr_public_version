from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models.credit import CreditsLedger


class InsufficientCreditsError(Exception):
    pass


@dataclass
class DebitResult:
    balance: int
    already_processed: bool = False


def ensure_org_row(db: Session, org_id: uuid.UUID) -> None:
    db.execute(text("""
        INSERT INTO org_credits (org_id)
        VALUES (:org_id)
        ON CONFLICT (org_id) DO NOTHING
    """), {"org_id": str(org_id)})


def get_balance(db: Session, org_id: uuid.UUID) -> int:
    row = db.execute(text("SELECT balance FROM org_credits WHERE org_id = :org_id"), {"org_id": str(org_id)}).first()
    return int(row[0]) if row else 0


def debit_if_possible(
    db: Session,
    org_id: uuid.UUID,
    amount: int,
    *,
    reason: str,
    idempotency_key: str | None = None,
) -> DebitResult:
    if amount <= 0:
        raise ValueError("amount must be positive")

    ensure_org_row(db, org_id)

    # Idempotent path: pre-insert ledger to serialize on unique key
    if idempotency_key:
        try:
            db.add(CreditsLedger(org_id=org_id, delta=-amount, reason=reason, idempotency_key=idempotency_key))
            db.flush()
        except IntegrityError:
            db.rollback()
            # Already processed by another request
            bal = get_balance(db, org_id)
            return DebitResult(balance=bal, already_processed=True)

        res = db.execute(
            text(
                """
                UPDATE org_credits
                SET balance = balance - :amount, updated_at = now()
                WHERE org_id = :org_id AND balance >= :amount
                RETURNING balance
                """
            ),
            {"org_id": str(org_id), "amount": int(amount)},
        ).first()
        if not res:
            # rollback ledger insert
            db.rollback()
            raise InsufficientCreditsError()
        db.commit()
        return DebitResult(balance=int(res[0]))

    # Non-idempotent path: attempt atomic update, then write ledger
    res = db.execute(
        text(
            """
            UPDATE org_credits
            SET balance = balance - :amount, updated_at = now()
            WHERE org_id = :org_id AND balance >= :amount
            RETURNING balance
            """
        ),
        {"org_id": str(org_id), "amount": int(amount)},
    ).first()
    if not res:
        db.rollback()
        raise InsufficientCreditsError()

    db.add(CreditsLedger(org_id=org_id, delta=-amount, reason=reason, idempotency_key=idempotency_key))
    db.commit()
    return DebitResult(balance=int(res[0]))


def refund(db: Session, org_id: uuid.UUID, amount: int, *, reason: str, idempotency_key: str | None = None) -> int:
    if amount <= 0:
        raise ValueError("amount must be positive")
    ensure_org_row(db, org_id)

    # Idempotent refund by ledger unique key
    if idempotency_key:
        try:
            db.add(CreditsLedger(org_id=org_id, delta=+amount, reason=reason, idempotency_key=idempotency_key))
            db.flush()
        except IntegrityError:
            db.rollback()
            return get_balance(db, org_id)

        res = db.execute(
            text(
                """
                UPDATE org_credits
                SET balance = balance + :amount, updated_at = now()
                WHERE org_id = :org_id
                RETURNING balance
                """
            ),
            {"org_id": str(org_id), "amount": int(amount)},
        ).first()
        db.commit()
        return int(res[0]) if res else get_balance(db, org_id)

    # Non-idempotent refund
    res = db.execute(
        text(
            """
            UPDATE org_credits
            SET balance = balance + :amount, updated_at = now()
            WHERE org_id = :org_id
            RETURNING balance
            """
        ),
        {"org_id": str(org_id), "amount": int(amount)},
    ).first()
    db.add(CreditsLedger(org_id=org_id, delta=+amount, reason=reason, idempotency_key=idempotency_key))
    db.commit()
    return int(res[0]) if res else get_balance(db, org_id)
