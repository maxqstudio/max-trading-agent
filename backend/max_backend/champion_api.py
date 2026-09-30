from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .promotion_service import (
    champion_detail,
    promote_strategy_challenger,
    promotion_detail,
    promotion_history,
)
from .champion_store import current_champion_summary


class PromotionConfirmation(BaseModel):
    expected_challenger_manifest_sha256: str
    expected_current_champion_id: str | None = None
    confirmed: bool


router = APIRouter(tags=["champion"])


@router.get("/api/champion")
def get_current_champion() -> dict:
    try:
        return champion_detail()
    except RuntimeError as exc:
        return {
            "status": "INTEGRITY_FAIL",
            "current": None,
            "reason": str(exc),
            "live_authority": "NONE",
        }


@router.get("/api/champion/summary")
def get_champion_summary() -> dict:
    return current_champion_summary()


@router.get("/api/promotions")
def get_promotions() -> list[dict]:
    return promotion_history()


@router.get("/api/promotions/{promotion_id}")
def get_promotion_detail(promotion_id: str) -> dict:
    try:
        return promotion_detail(promotion_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Promotion not found") from exc


@router.post("/api/challengers/{challenger_id}/promote")
def promote(challenger_id: str, payload: PromotionConfirmation) -> dict:
    try:
        return promote_strategy_challenger(
            challenger_id,
            expected_challenger_manifest_sha256=(
                payload.expected_challenger_manifest_sha256
            ),
            expected_current_champion_id=payload.expected_current_champion_id,
            confirmed=payload.confirmed,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        message = str(exc)
        conflict = (
            "STALE",
            "ACTIVE",
            "ALREADY",
            "INTEGRITY",
            "BLOCKED",
            "ROLLBACK",
            "NOT_ACTIVE",
        )
        code = 409 if any(token in message for token in conflict) else 400
        raise HTTPException(status_code=code, detail=message) from exc
