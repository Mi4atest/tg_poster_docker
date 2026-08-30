"""CRUD и выбор due-позиций watchlist рынка Avito."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

from app.api.models.avito_market_snapshot import AvitoMarketSnapshot
from app.api.models.avito_market_watchlist_item import AvitoMarketWatchlistItem
from app.api.models.product import Product
from app.db.database import SessionLocal
from app.utils.iphone_market_query import SUPPORTED_MEMORY_GB
from app.utils.iphone_parser import parse_iphone_memory, parse_iphone_model
from app.utils.price_change import price_string_to_int_rub


_NEW_COLLECTIONS = {"iPhone новые", "Airpods", "Apple Watch", "iPad", "custom"}
_VINTAGE_MODELS = frozenset(
    {"iPhone X", "iPhone XR", "iPhone XS", "iPhone XS Max"}
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _item_dict(row: AvitoMarketWatchlistItem, snapshot: Optional[AvitoMarketSnapshot] = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "model": row.model,
        "memory_gb": row.memory_gb,
        "tier": row.tier,
        "enabled": bool(row.enabled),
        "source": row.source,
        "last_snapshot_id": row.last_snapshot_id,
        "last_attempt_at": row.last_attempt_at,
        "last_refreshed_at": row.last_refreshed_at,
        "next_refresh_at": row.next_refresh_at,
        "created_at": row.created_at,
        "median_rub": None,
        "fetched_at": None,
        "expires_at": None,
        "snapshot_status": None,
    }
    if snapshot is not None:
        data["median_rub"] = snapshot.median_rub
        data["fetched_at"] = snapshot.fetched_at
        data["expires_at"] = snapshot.expires_at
        data["snapshot_status"] = snapshot.status
        if data["last_snapshot_id"] is None:
            data["last_snapshot_id"] = snapshot.id
    return data


def catalog_memory_to_gb(raw: Optional[str]) -> Optional[int]:
    value = (raw or "").strip()
    if not value:
        return None
    lowered = value.lower().replace("тб", "tb")
    if lowered in {"1tb", "1 tb"}:
        return 1024
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number in SUPPORTED_MEMORY_GB else None


def is_vintage_market_model(model: str) -> bool:
    return str(model or "").strip() in _VINTAGE_MODELS


@dataclass(frozen=True)
class ShopPriceRange:
    """Min–max цен активных б/у в магазине для одной конфигурации."""

    count: int
    min_rub: int
    max_rub: int


def used_catalog_config(
    name: str,
    collection: Optional[str] = None,
) -> Optional[tuple[str, int]]:
    """Модель и память б/у-товара, если название однозначно разбирается."""
    if (collection or "").strip() in _NEW_COLLECTIONS:
        return None
    model = parse_iphone_model(name or "")
    memory = catalog_memory_to_gb(parse_iphone_memory(name or ""))
    if not model or memory is None:
        return None
    return (model, memory)


def shop_price_range_from_rows(
    rows: list[tuple[str, Optional[str], Optional[str]]],
    model: str,
    memory_gb: int,
) -> Optional[ShopPriceRange]:
    """Вилка цен по уже загруженным строкам каталога (без vintage-фильтра)."""
    want_model = str(model or "")
    want_memory = int(memory_gb)
    prices: list[int] = []
    for name, collection, price in rows:
        matched = used_catalog_config(name, collection)
        if matched is None:
            continue
        got_model, got_memory = matched
        if got_model != want_model or got_memory != want_memory:
            continue
        rub = price_string_to_int_rub(price)
        if rub is None:
            continue
        prices.append(rub)
    if not prices:
        return None
    return ShopPriceRange(count=len(prices), min_rub=min(prices), max_rub=max(prices))


def get_used_shop_price_range(model: str, memory_gb: int) -> Optional[ShopPriceRange]:
    """Активные б/у той же модели и памяти, что в оценке Avito."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Product.name, Product.collection_name, Product.price)
            .filter(Product.status == "active")
            .all()
        )
        return shop_price_range_from_rows(
            [(str(name or ""), collection, price) for name, collection, price in rows],
            model,
            int(memory_gb),
        )
    finally:
        db.close()


def list_watchlist_items() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = db.query(AvitoMarketWatchlistItem).all()
        result: list[dict[str, Any]] = []
        for row in rows:
            snapshot = None
            if row.last_snapshot_id:
                snapshot = (
                    db.query(AvitoMarketSnapshot)
                    .filter(AvitoMarketSnapshot.id == row.last_snapshot_id)
                    .first()
                )
            if snapshot is None:
                snapshot = (
                    db.query(AvitoMarketSnapshot)
                    .filter(
                        AvitoMarketSnapshot.model == row.model,
                        AvitoMarketSnapshot.memory_gb == row.memory_gb,
                        AvitoMarketSnapshot.status == "success",
                    )
                    .order_by(AvitoMarketSnapshot.fetched_at.desc())
                    .first()
                )
            result.append(_item_dict(row, snapshot))
        return result
    finally:
        db.close()


def get_watchlist_item(item_id: int) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketWatchlistItem)
            .filter(AvitoMarketWatchlistItem.id == int(item_id))
            .first()
        )
        if row is None:
            return None
        snapshot = None
        if row.last_snapshot_id:
            snapshot = (
                db.query(AvitoMarketSnapshot)
                .filter(AvitoMarketSnapshot.id == row.last_snapshot_id)
                .first()
            )
        return _item_dict(row, snapshot)
    finally:
        db.close()


def get_watchlist_item_by_config(model: str, memory_gb: int) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketWatchlistItem)
            .filter(
                AvitoMarketWatchlistItem.model == model,
                AvitoMarketWatchlistItem.memory_gb == int(memory_gb),
            )
            .first()
        )
        return _item_dict(row) if row else None
    finally:
        db.close()


def add_watchlist_item(
    model: str,
    memory_gb: int,
    *,
    tier: str = "daily",
    source: str = "manual",
    last_snapshot_id: Optional[int] = None,
    next_refresh_at: Optional[datetime] = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = AvitoMarketWatchlistItem(
            model=model,
            memory_gb=int(memory_gb),
            tier="slow" if tier == "slow" else "daily",
            enabled=True,
            source=(source or "manual")[:24],
            last_snapshot_id=last_snapshot_id,
            next_refresh_at=next_refresh_at or _utcnow(),
            created_at=_utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _item_dict(row)
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(AvitoMarketWatchlistItem)
            .filter(
                AvitoMarketWatchlistItem.model == model,
                AvitoMarketWatchlistItem.memory_gb == int(memory_gb),
            )
            .first()
        )
        return _item_dict(existing) if existing else {}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_watchlist_item(item_id: int) -> bool:
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketWatchlistItem)
            .filter(AvitoMarketWatchlistItem.id == int(item_id))
            .first()
        )
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_watchlist_item(
    item_id: int,
    *,
    tier: Optional[str] = None,
    enabled: Optional[bool] = None,
    last_snapshot_id: Optional[int] = None,
    last_attempt_at: Optional[datetime] = None,
    last_refreshed_at: Optional[datetime] = None,
    next_refresh_at: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketWatchlistItem)
            .filter(AvitoMarketWatchlistItem.id == int(item_id))
            .first()
        )
        if row is None:
            return None
        if tier is not None:
            row.tier = "slow" if tier == "slow" else "daily"
        if enabled is not None:
            row.enabled = bool(enabled)
        if last_snapshot_id is not None:
            row.last_snapshot_id = last_snapshot_id
        if last_attempt_at is not None:
            row.last_attempt_at = last_attempt_at
        if last_refreshed_at is not None:
            row.last_refreshed_at = last_refreshed_at
        if next_refresh_at is not None:
            row.next_refresh_at = next_refresh_at
        db.commit()
        db.refresh(row)
        return _item_dict(row)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_due_watchlist_item(now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    moment = now or _utcnow()
    db = SessionLocal()
    try:
        row = (
            db.query(AvitoMarketWatchlistItem)
            .filter(
                AvitoMarketWatchlistItem.enabled.is_(True),
                (
                    AvitoMarketWatchlistItem.next_refresh_at.is_(None)
                    | (AvitoMarketWatchlistItem.next_refresh_at <= moment)
                ),
            )
            .order_by(
                AvitoMarketWatchlistItem.next_refresh_at.is_(None).desc(),
                AvitoMarketWatchlistItem.next_refresh_at.asc(),
            )
            .first()
        )
        return _item_dict(row) if row else None
    finally:
        db.close()


def list_watchlist_keys() -> set[tuple[str, int]]:
    db = SessionLocal()
    try:
        rows = db.query(
            AvitoMarketWatchlistItem.model,
            AvitoMarketWatchlistItem.memory_gb,
        ).all()
        return {(str(model), int(memory)) for model, memory in rows}
    finally:
        db.close()


def list_used_catalog_configs() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Product.name, Product.collection_name)
            .filter(Product.status == "active")
            .all()
        )
        counts: dict[tuple[str, int], int] = {}
        for name, collection in rows:
            matched = used_catalog_config(name or "", collection)
            if matched is None:
                continue
            model, memory = matched
            if is_vintage_market_model(model):
                continue
            key = (model, memory)
            counts[key] = counts.get(key, 0) + 1
        return [
            {"model": model, "memory_gb": memory, "product_count": count}
            for (model, memory), count in counts.items()
        ]
    finally:
        db.close()
