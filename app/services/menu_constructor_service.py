"""
Дерево меню «Список новых»: хардкод-пути, пользовательские кнопки, скрытие, счётчики.
Без импорта aiogram / handlers (избегаем циклов).
"""
from __future__ import annotations

import base64
from html import escape
import logging
import re
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.models.new_menu_button import NewMenuButton
from app.api.models.product import Product
from app.api.models.post import Post
from app.db.database import SessionLocal
from app.services.settings_service import get_settings_service
from app.utils.product_label import button_label_for_product, render_label, describe_product
from app.utils.iphone_parser import (
    parse_iphone_model,
    get_model_display_name,
    get_iphone_version_from_model,
    parse_iphone_memory,
    parse_iphone_storage_type,
    get_short_model_key_for_new,
    parse_iphone_color_key,
)
from app.utils.vk_market_sync import POST_ID_FOR_NEW_PRODUCTS

logger = logging.getLogger(__name__)


NEW_COLLECTION_VALUES = {"iPhone новые", "Airpods", "Apple Watch", "iPad"}
CUSTOM_COLLECTION = "custom"

AIRPODS_ORDER = [
    "AirPods 3",
    "AirPods 3 Magsafe",
    "AirPods 4",
    "AirPods 4 ANC",
    "AirPods Pro 2",
    "AirPods Pro 3",
]
AIRPODS_KEY = {
    "AirPods 3": "airpods_3",
    "AirPods 3 Magsafe": "airpods_3_magsafe",
    "AirPods 4": "airpods_4",
    "AirPods 4 ANC": "airpods_4_anc",
    "AirPods Pro 2": "airpods_pro_2",
    "AirPods Pro 3": "airpods_pro_3",
}
WATCH_CATS = ["SE 2", "SE 3", "11"]
WATCH_KEY = {"SE 2": "se_2", "SE 3": "se_3", "11": "11"}
WATCH_SIZES = ["40mm", "44mm"]
IPAD_ORDER = ["iPad 11", "iPad Air"]
IPAD_KEY = {"iPad 11": "ipad_11", "iPad Air": "ipad_air"}


def _path_parent(path: str) -> Optional[str]:
    if path == "root" or not path:
        return None
    if ">" not in path:
        return "root"
    return path.rsplit(">", 1)[0]


def custom_node_path(button_id: int) -> str:
    return f"custom:{button_id}"


def encode_menu_path_token(path: str) -> str:
    """Короткий токен для callback new_npr_* (возврат к хардкод-экрану)."""
    raw = zlib.compress(path.encode("utf-8"), 9)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_menu_path_token(token: str) -> str:
    pad = "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode((token + pad).encode("ascii"))
    return zlib.decompress(raw).decode("utf-8")


def _get_hidden_keys() -> Set[str]:
    data = get_settings_service().get_all()
    mc = data.get("new_menu_constructor") or {}
    return set(mc.get("hidden_keys") or [])


def set_hidden_keys(keys: List[str]) -> None:
    get_settings_service().update({"new_menu_constructor": {"hidden_keys": list(keys)}})


def toggle_hidden(path: str) -> bool:
    """Переключает скрытие узла. Возвращает новое состояние: True если скрыт."""
    cur = _get_hidden_keys()
    s = set(cur)
    if path in s:
        s.remove(path)
        set_hidden_keys(list(s))
        logger.info("menu_constructor: show %s", path)
        return False
    s.add(path)
    set_hidden_keys(list(s))
    logger.info("menu_constructor: hide %s", path)
    return True


def is_hidden(path: str) -> bool:
    return path in _get_hidden_keys()


def _get_label_overrides() -> Dict[str, str]:
    data = get_settings_service().get_all()
    mc = data.get("new_menu_constructor") or {}
    raw = mc.get("label_overrides") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


def effective_hardcoded_label(path: str, default: str) -> str:
    """Подпись узла в «Список новых» / конструкторе с учётом пользовательского переименования."""
    ov = _get_label_overrides().get(path)
    return ov if ov else default


def set_label_override(path: str, label: Optional[str]) -> None:
    """Задать или сбросить (label пустой) свою подпись для стандартного узла по path."""
    cur = dict(_get_label_overrides())
    t = (label or "").strip()
    if t:
        cur[path] = t[:128]
    else:
        cur.pop(path, None)
    get_settings_service().update({"new_menu_constructor": {"label_overrides": cur}})


def get_custom_button_label(db: Session, button_id: Optional[int]) -> Optional[str]:
    if not button_id:
        return None
    b = db.query(NewMenuButton).filter(NewMenuButton.id == int(button_id)).first()
    if not b or getattr(b, "is_service", False):
        return None
    lbl = (b.label or "").strip()
    from app.utils.product_label import is_usable_menu_button_label

    return lbl if is_usable_menu_button_label(lbl) else None


def is_iphone_hardcoded_menu_path(path: str) -> bool:
    """Узлы дерева iPhone (хардкод-пути)."""
    return (path or "").startswith("root>cat>iPhone")


def is_iphone_hardcoded_intermediate_path(path: str) -> bool:
    """Промежуточный узел iPhone (не лист): навигация без кнопок-товаров."""
    if not is_iphone_hardcoded_menu_path(path):
        return False
    return not is_hardcoded_leaf_with_products(path)


def list_products_at_hardcoded_leaf(db: Session, path: str) -> List[Dict[str, Any]]:
    """Custom-товары, привязанные ровно к hardcoded-листу (parent_path служебной кнопки == path)."""
    path = (path or "").strip()
    if not is_hardcoded_leaf_with_products(path):
        return []
    buttons = _load_buttons(db)
    btn_map = {b.id: b for b in buttons}
    out: List[Dict[str, Any]] = []
    for p in (
        db.query(Product)
        .filter(Product.collection_name == CUSTOM_COLLECTION, Product.custom_button_id.isnot(None))
        .order_by(Product.id)
        .all()
    ):
        bid = p.custom_button_id
        if not bid:
            continue
        btn = btn_map.get(int(bid))
        if btn and (btn.parent_path or "") == path:
            out.append(_product_to_dict(p))
    return out


def detach_custom_product(db: Session, product_id: int) -> bool:
    """Отвязать кастом-товар от меню (custom_button_id → NULL), запись в БД остаётся."""
    p = db.query(Product).filter(Product.id == int(product_id)).first()
    if not p or (p.collection_name or "").strip() != CUSTOM_COLLECTION:
        return False
    p.custom_button_id = None
    db.commit()
    logger.info("menu_constructor: detached product %s from menu", product_id)
    return True


def list_custom_products_at_node(db: Session, path: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Кастом-товары на узле (лист, custom:N) — для управления в редакторе."""
    if path.startswith("custom:"):
        try:
            bid = int(path.split(":", 1)[1])
        except ValueError:
            return []
        return list_products_for_custom_leaf(db, bid)[:limit]
    if is_hardcoded_leaf_with_products(path):
        return list_products_at_hardcoded_leaf(db, path)[:limit]
    return _detachable_custom_products_exact_path(db, path, limit)


def _detachable_custom_products_exact_path(db: Session, path: str, limit: int = 12) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in (
        db.query(Product)
        .filter(Product.collection_name == CUSTOM_COLLECTION, Product.custom_button_id.isnot(None))
        .order_by(Product.id.desc())
        .all()
    ):
        bid = p.custom_button_id
        if not bid:
            continue
        if custom_node_path(int(bid)) == path:
            out.append(_product_to_dict(p))
        if len(out) >= limit:
            break
    return out


def list_detachable_custom_products_at_path(db: Session, path: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Алиас для обратной совместимости."""
    return list_custom_products_at_node(db, path, limit)


@dataclass
class MenuNode:
    path: str
    label: str
    kind: str  # "hardcoded" | "custom"
    count: int
    hidden: bool
    custom_id: Optional[int] = None
    emoji: Optional[str] = None


def _product_to_dict(p: Product) -> Dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name or "",
        "display_label": (p.display_label or "").strip() or None,
        "price": p.price,
        "collection_name": (p.collection_name or "").strip(),
        "status": p.status,
        "vk_product_link": p.vk_product_link,
        "vk_product_id": p.vk_product_id,
        "availability_status": p.availability_status,
        "custom_button_id": p.custom_button_id,
    }


def load_new_products_dicts(db: Session) -> List[Dict[str, Any]]:
    rows = (
        db.query(Product)
        .filter(
            or_(
                Product.collection_name.in_(list(NEW_COLLECTION_VALUES)),
                Product.collection_name == CUSTOM_COLLECTION,
            )
        )
        .all()
    )
    return [_product_to_dict(p) for p in rows]


def _filter_classical(items: List[dict], collection_value: Optional[str] = None) -> List[dict]:
    lst = [p for p in items if (p.get("collection_name") or "").strip() in NEW_COLLECTION_VALUES]
    if collection_value:
        lst = [p for p in lst if (p.get("collection_name") or "").strip() == collection_value]
    return lst


def _iphone_version_counts(products: List[dict]) -> Dict[str, int]:
    counts = {v: 0 for v in ["12", "13", "14", "15", "16", "17"]}
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        if ver and ver in counts:
            counts[ver] += 1
        elif ver in ("X", "SE"):
            continue
        elif not ver and name:
            nl = name.lower()
            if "iphone 12" in nl:
                counts["12"] = counts.get("12", 0) + 1
            elif "iphone 14" in nl:
                counts["14"] = counts.get("14", 0) + 1
    return counts


def _iphone_model_counts(products: List[dict], version: str) -> Dict[str, int]:
    VERSION_MODELS = {
        "12": ["12", "12 mini", "12 Pro", "12 Pro Max"],
        "13": ["13", "13 mini", "13 Pro", "13 Pro Max"],
        "14": ["14", "14 Plus", "14 Pro", "14 Pro Max"],
        "15": ["15", "15 Plus", "15 Pro", "15 Pro Max"],
        "16": ["16", "16E", "16 Plus", "16 Pro", "16 Pro Max"],
        "17": ["Air", "17", "17E", "17 Pro", "17 Pro Max"],
    }
    order = VERSION_MODELS.get(version, [])
    counts: Dict[str, int] = {}
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        if ver != version:
            continue
        disp = get_model_display_name(model) if model else None
        if disp:
            counts[disp] = counts.get(disp, 0) + 1
    return {k: counts.get(k, 0) for k in order if k in counts}


def _display_to_model_key(display: str) -> str:
    return display.replace(" ", "_").lower()


def _iphone_memory_counts(products: List[dict], version: str, model_key: str) -> Dict[str, int]:
    order = ["64", "128", "256", "512", "1Tb"]
    counts: Dict[str, int] = {}
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        if ver != version or short != model_key:
            continue
        mem = parse_iphone_memory(name)
        if mem:
            counts[mem] = counts.get(mem, 0) + 1
    return {k: counts.get(k, 0) for k in order if counts.get(k, 0) > 0}


def _iphone_storage_counts(products: List[dict], version: str, model_key: str, memory_key: str) -> Dict[str, int]:
    counts = {"esim": 0, "1+1": 0, "2sim": 0}
    memory_norm = "1Tb" if (memory_key or "").lower() == "1tb" else memory_key
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        mem = parse_iphone_memory(name)
        if ver != version or short != model_key:
            continue
        if memory_norm == "1Tb" and mem != "1Tb":
            continue
        if memory_norm != "1Tb" and (mem or "") != memory_norm:
            continue
        st = parse_iphone_storage_type(name)
        if st in counts:
            counts[st] += 1
        elif version == "17" and st is None:
            mk = (model_key or "").lower()
            if mk == "17" or "17_pro" in mk or "17_promax" in mk:
                counts["1+1"] += 1
    return counts


def _iphone_products_for_storage(
    products: List[dict], version: str, model_key: str, memory_key: str, storage_key: str
) -> List[dict]:
    storage_norm = storage_key.replace("p", "+") if "p" in storage_key else storage_key
    memory_norm = "1Tb" if memory_key == "1tb" else memory_key
    out = []
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        mem = parse_iphone_memory(name)
        st = parse_iphone_storage_type(name)
        if ver != version or short != model_key:
            continue
        if memory_norm == "1Tb" and mem != "1Tb":
            continue
        if memory_norm != "1Tb" and (mem or "") != memory_norm:
            continue
        if storage_norm == "1+1":
            if st == "1+1":
                out.append(p)
            elif version == "17" and st is None:
                mk = (model_key or "").lower()
                if mk == "17" or "17_pro" in mk or "17_promax" in mk:
                    out.append(p)
        elif st == storage_norm:
            out.append(p)
    return out


def _iphone_products_for_memory(products: List[dict], version: str, model_key: str, memory_key: str) -> List[dict]:
    memory_norm = "1Tb" if (memory_key or "").lower() == "1tb" else memory_key
    out = []
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        mem = parse_iphone_memory(name)
        if ver != version or short != model_key:
            continue
        if memory_norm == "1Tb" and mem != "1Tb":
            continue
        if memory_norm != "1Tb" and (mem or "") != memory_norm:
            continue
        out.append(p)
    return out


def _iphone_products_for_model(products: List[dict], version: str, model_key: str) -> List[dict]:
    out = []
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        short = get_short_model_key_for_new(model or "")
        if ver != version or short != model_key:
            continue
        out.append(p)
    return out


def _iphone_products_for_version(products: List[dict], version: str) -> List[dict]:
    out = []
    for p in products:
        name = p.get("name", "")
        model = parse_iphone_model(name)
        ver = get_iphone_version_from_model(model) if model else None
        if ver == version:
            out.append(p)
        elif version in ("12", "14") and (name or "").lower().count(f"iphone {version}") > 0:
            out.append(p)
    return out


def _parse_airpods_model(name: str) -> Optional[str]:
    nl = name.lower()
    if "airpods pro 3" in nl or "pro 3" in nl:
        return "AirPods Pro 3"
    if "airpods pro 2" in nl or "pro 2" in nl:
        return "AirPods Pro 2"
    if "airpods 4 anc" in nl or "4 anc" in nl:
        return "AirPods 4 ANC"
    if "airpods 4" in nl:
        return "AirPods 4"
    if "airpods 3" in nl and "magsafe" in nl:
        return "AirPods 3 Magsafe"
    if "airpods 3" in nl:
        return "AirPods 3"
    return None


def _parse_apple_watch_category(name: str) -> Optional[str]:
    nl = name.lower()
    if "se 3" in nl or "se3" in nl:
        return "SE 3"
    if "se 2" in nl or "se2" in nl:
        return "SE 2"
    if "11" in nl and ("watch" in nl or "aw" in nl):
        return "11"
    return None


def _parse_apple_watch_size(name: str) -> Optional[str]:
    nl = name.lower()
    if "46mm" in nl or "46 mm" in nl:
        return "46mm"
    if "45mm" in nl or "45 mm" in nl:
        return "45mm"
    if "44mm" in nl or "44 mm" in nl:
        return "44mm"
    if "41mm" in nl or "41 mm" in nl:
        return "41mm"
    if "42mm" in nl or "42 mm" in nl:
        return "42mm"
    if "40mm" in nl or "40 mm" in nl:
        return "40mm"
    return None


def _parse_ipad_model(name: str) -> Optional[str]:
    nl = name.lower()
    if "ipad air" in nl:
        return "iPad Air"
    if "ipad 11" in nl or ("ipad" in nl and "11" in nl):
        return "iPad 11"
    return None


def _watch_size_options_for_category(cat: str) -> List[str]:
    """Доступные размеры по категории часов в конструкторе."""
    if cat == "11":
        return ["42mm", "46mm"]
    return ["40mm", "44mm"]


def _load_buttons(db: Session) -> List[NewMenuButton]:
    return db.query(NewMenuButton).order_by(NewMenuButton.parent_path, NewMenuButton.sort_order, NewMenuButton.id).all()


def classical_count_for_path(path: str, items: List[dict]) -> int:
    """Счёт только «классических» товаров ВК (без custom collection)."""
    if path.startswith("custom:"):
        return 0
    if path == "root":
        return len(_filter_classical(items))
    parts = path.split(">")
    if parts[:3] == ["root", "cat", "Airpods"] and len(parts) == 3:
        prods = _filter_classical(items, "Airpods")
        return len(prods)
    if parts[:3] == ["root", "cat", "Apple Watch"] and len(parts) == 3:
        return len(_filter_classical(items, "Apple Watch"))
    if parts[:3] == ["root", "cat", "iPad"] and len(parts) == 3:
        return len(_filter_classical(items, "iPad"))
    if parts[:3] == ["root", "cat", "iPhone"] and len(parts) == 3:
        return len(_filter_classical(items, "iPhone новые"))

    iphone_new = _filter_classical(items, "iPhone новые")
    if parts[:4] == ["root", "cat", "iPhone", "ver"] and len(parts) == 5:
        ver = parts[4]
        vc = _iphone_version_counts(iphone_new)
        return vc.get(ver, 0)
    if parts[:4] == ["root", "cat", "iPhone", "ver"] and len(parts) == 7 and parts[5] == "md":
        ver, mk = parts[4], parts[6]
        mc = _iphone_model_counts(iphone_new, ver)
        for disp, c in mc.items():
            if _display_to_model_key(disp) == mk:
                return c
        return 0
    if parts[:4] == ["root", "cat", "iPhone", "ver"] and len(parts) == 9 and parts[5] == "md" and parts[7] == "mem":
        ver, mk, mem = parts[4], parts[6], parts[8]
        mm = _iphone_memory_counts(iphone_new, ver, mk)
        mem_key = "1Tb" if mem.lower() == "1tb" else mem
        return mm.get(mem_key, 0)
    if (
        len(parts) >= 11
        and parts[:4] == ["root", "cat", "iPhone", "ver"]
        and parts[5] == "md"
        and parts[7] == "mem"
        and parts[9] == "stor"
    ):
        ver, mk, mem, stor = parts[4], parts[6], parts[8], parts[10]
        plist = _iphone_products_for_storage(iphone_new, ver, mk, mem, stor)
        return len(plist)
    if len(parts) >= 9 and parts[:4] == ["root", "cat", "iPhone", "ver"] and parts[5] == "md" and parts[7] == "mem":
        ver, mk, mem = parts[4], parts[6], parts[8]
        plist = _iphone_products_for_memory(iphone_new, ver, mk, mem)
        if ver in ("12", "13", "14", "15", "16"):
            return len(plist)
        return 0

    if parts[:4] == ["root", "cat", "Airpods", "md"] and len(parts) == 5:
        mk = parts[4]
        prods = _filter_classical(items, "Airpods")
        inv = {v: k for k, v in AIRPODS_KEY.items()}
        model_name = inv.get(mk)
        if not model_name:
            return 0
        return sum(1 for p in prods if _parse_airpods_model(p.get("name", "")) == model_name)

    if parts[:4] == ["root", "cat", "Apple Watch", "wc"] and len(parts) == 5:
        ck = parts[4]
        inv = {v: k for k, v in WATCH_KEY.items()}
        cat = inv.get(ck)
        if not cat:
            return 0
        prods = _filter_classical(items, "Apple Watch")
        return sum(1 for p in prods if _parse_apple_watch_category(p.get("name", "")) == cat)

    if (
        len(parts) == 7
        and parts[:4] == ["root", "cat", "Apple Watch", "wc"]
        and parts[5] == "sz"
    ):
        ck, szk = parts[4], parts[6]
        inv = {v: k for k, v in WATCH_KEY.items()}
        cat = inv.get(ck)
        if not cat:
            return 0
        prods = _filter_classical(items, "Apple Watch")
        n = 0
        for p in prods:
            if _parse_apple_watch_category(p.get("name", "")) != cat:
                continue
            sz = _parse_apple_watch_size(p.get("name", ""))
            if sz and sz.replace(" ", "").lower() == szk.replace("_", ""):
                n += 1
        return n

    if parts[:4] == ["root", "cat", "iPad", "md"] and len(parts) == 5:
        mk = parts[4]
        inv = {v: k for k, v in IPAD_KEY.items()}
        model_name = inv.get(mk)
        if not model_name:
            return 0
        prods = _filter_classical(items, "iPad")
        return sum(1 for p in prods if _parse_ipad_model(p.get("name", "")) == model_name)

    return 0


def _hardcoded_prefixes(pp: str) -> Set[str]:
    if not pp or pp == "root":
        return {"root"}
    parts = pp.split(">")
    return {">".join(parts[: i]) for i in range(1, len(parts) + 1)}


def _paths_for_product_button(button_id: int, btn_map: Dict[int, NewMenuButton]) -> Set[str]:
    """Все узлы меню (хардкод-префиксы + custom:id), к которым относится товар."""
    out: Set[str] = set()
    bid: Optional[int] = button_id
    while bid is not None:
        b = btn_map.get(bid)
        if not b:
            break
        out.add(custom_node_path(bid))
        pp = b.parent_path or "root"
        if pp.startswith("custom:"):
            bid = int(pp.split(":", 1)[1])
            continue
        out |= _hardcoded_prefixes(pp)
        bid = None
    return out


def custom_count_for_path(path: str, items: List[dict], btn_map: Dict[int, NewMenuButton]) -> int:
    n = 0
    for p in items:
        bid = p.get("custom_button_id")
        if not bid:
            continue
        if path in _paths_for_product_button(int(bid), btn_map):
            n += 1
    return n


def total_count_for_path(db: Session, path: str, items: List[dict]) -> int:
    buttons = _load_buttons(db)
    btn_map = {b.id: b for b in buttons}
    return classical_count_for_path(path, items) + custom_count_for_path(path, items, btn_map)


def collect_products_for_path(
    db: Session, path: str, items: Optional[List[dict]] = None
) -> List[Dict[str, Any]]:
    """Классические + custom товары в поддереве path (для текстовых списков в навигации)."""
    if items is None:
        items = load_new_products_dicts(db)
    parts = path.split(">")
    plist: List[dict] = []

    if parts[:4] == ["root", "cat", "iPhone", "ver"] and len(parts) == 5:
        iphone_new = _filter_classical(items, "iPhone новые")
        plist = list(_iphone_products_for_version(iphone_new, parts[4]))
    elif parts[:4] == ["root", "cat", "iPhone", "ver"] and len(parts) == 7 and parts[5] == "md":
        iphone_new = _filter_classical(items, "iPhone новые")
        plist = list(_iphone_products_for_model(iphone_new, parts[4], parts[6]))
    elif (
        parts[:4] == ["root", "cat", "iPhone", "ver"]
        and len(parts) == 9
        and parts[5] == "md"
        and parts[7] == "mem"
    ):
        iphone_new = _filter_classical(items, "iPhone новые")
        plist = list(_iphone_products_for_memory(iphone_new, parts[4], parts[6], parts[8]))
    elif parts[:3] == ["root", "cat", "Airpods"] and len(parts) == 3:
        plist = list(_filter_classical(items, "Airpods"))
    elif parts[:3] == ["root", "cat", "Airpods"] and len(parts) == 5 and parts[3] == "md":
        inv = {v: k for k, v in AIRPODS_KEY.items()}
        model_name = inv.get(parts[4])
        if model_name:
            for p in _filter_classical(items, "Airpods"):
                if _parse_airpods_model(p.get("name", "")) == model_name:
                    plist.append(p)
    elif parts[:3] == ["root", "cat", "Apple Watch"] and len(parts) == 3:
        plist = list(_filter_classical(items, "Apple Watch"))
    elif parts[:3] == ["root", "cat", "Apple Watch"] and len(parts) == 5 and parts[3] == "wc":
        inv = {v: k for k, v in WATCH_KEY.items()}
        cat = inv.get(parts[4])
        if cat:
            for p in _filter_classical(items, "Apple Watch"):
                if _parse_apple_watch_category(p.get("name", "")) == cat:
                    plist.append(p)
    elif (
        len(parts) == 7
        and parts[:3] == ["root", "cat", "Apple Watch"]
        and parts[3] == "wc"
        and parts[5] == "sz"
    ):
        inv = {v: k for k, v in WATCH_KEY.items()}
        cat = inv.get(parts[4])
        szk = parts[6]
        if cat:
            for p in _filter_classical(items, "Apple Watch"):
                if _parse_apple_watch_category(p.get("name", "")) != cat:
                    continue
                sz = _parse_apple_watch_size(p.get("name", ""))
                if sz and sz.replace(" ", "").lower() == szk.replace("_", ""):
                    plist.append(p)
    elif parts[:3] == ["root", "cat", "iPad"] and len(parts) == 3:
        plist = list(_filter_classical(items, "iPad"))
    elif parts[:3] == ["root", "cat", "iPad"] and len(parts) == 5 and parts[3] == "md":
        inv = {v: k for k, v in IPAD_KEY.items()}
        model_name = inv.get(parts[4])
        if model_name:
            for p in _filter_classical(items, "iPad"):
                if _parse_ipad_model(p.get("name", "")) == model_name:
                    plist.append(p)

    seen: Set[int] = {int(p["id"]) for p in plist if p.get("id") is not None}
    for p in list_custom_products_for_path(db, path):
        pid = p.get("id")
        if pid is None or int(pid) in seen:
            continue
        plist.append(p)
        seen.add(int(pid))
    return plist


def _tc(path: str, items: List[dict], btn_map: Dict[int, NewMenuButton]) -> int:
    return classical_count_for_path(path, items) + custom_count_for_path(path, items, btn_map)


def _hardcoded_children(
    path: str,
    items: List[dict],
    btn_map: Dict[int, NewMenuButton],
    editor: bool = False,
) -> List[MenuNode]:
    hidden = _get_hidden_keys()
    iphone_new = _filter_classical(items, "iPhone новые")
    out: List[MenuNode] = []

    if path == "root":
        cats = [
            ("root>cat>Airpods", "Airpods", "🎧"),
            ("root>cat>Apple Watch", "Apple Watch", "⌚"),
            ("root>cat>iPad", "iPad", "📱"),
            ("root>cat>iPhone", "iPhone", "📱"),
        ]
        for pth, lbl, em in cats:
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, lbl),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                    emoji=em,
                )
            )
        return out

    if path == "root>cat>Airpods":
        prods = _filter_classical(items, "Airpods")
        model_counts: Dict[str, int] = {}
        for p in prods:
            m = _parse_airpods_model(p.get("name", ""))
            if m:
                model_counts[m] = model_counts.get(m, 0) + 1
        for model in AIRPODS_ORDER:
            mk = AIRPODS_KEY[model]
            pth = f"root>cat>Airpods>md>{mk}"
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, model),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                )
            )
        return out

    if path == "root>cat>Apple Watch":
        category_counts: Dict[str, int] = {}
        for p in _filter_classical(items, "Apple Watch"):
            cn = _parse_apple_watch_category(p.get("name", ""))
            if cn:
                category_counts[cn] = category_counts.get(cn, 0) + 1
        for cat in WATCH_CATS:
            ck = WATCH_KEY[cat]
            pth = f"root>cat>Apple Watch>wc>{ck}"
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, f"AW {cat}"),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                )
            )
        return out

    if path.startswith("root>cat>Apple Watch>wc>") and "sz" not in path:
        ck = path.split(">")[-1]
        inv = {v: k for k, v in WATCH_KEY.items()}
        cat = inv.get(ck)
        if not cat:
            return out
        prods = _filter_classical(items, "Apple Watch")
        size_counts: Dict[str, int] = {}
        sample_names: List[str] = []
        for p in prods:
            if _parse_apple_watch_category(p.get("name", "")) != cat:
                continue
            if len(sample_names) < 10:
                sample_names.append((p.get("name") or "")[:140])
            sz = _parse_apple_watch_size(p.get("name", ""))
            if sz:
                size_counts[sz] = size_counts.get(sz, 0) + 1
        for size in _watch_size_options_for_category(cat):
            szk = size.lower().replace(" ", "")
            pth = f"{path}>sz>{szk}"
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, f"AW {cat} {size}"),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                )
            )
        return out

    if path == "root>cat>iPad":
        model_counts: Dict[str, int] = {}
        for p in _filter_classical(items, "iPad"):
            m = _parse_ipad_model(p.get("name", ""))
            if m:
                model_counts[m] = model_counts.get(m, 0) + 1
        for model in IPAD_ORDER:
            mk = IPAD_KEY[model]
            pth = f"root>cat>iPad>md>{mk}"
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, model),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                )
            )
        return out

    if path == "root>cat>iPhone":
        v_counts = _iphone_version_counts(iphone_new)
        for v in ["12", "13", "14", "15", "16", "17"]:
            pth = f"root>cat>iPhone>ver>{v}"
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, f"iPhone {v}"),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                    emoji="📱",
                )
            )
        return out

    if path.startswith("root>cat>iPhone>ver>") and ">md>" not in path:
        ver = path.split(">")[-1]
        m_counts = _iphone_model_counts(iphone_new, ver)
        VERSION_MODELS = {
            "12": ["12", "12 mini", "12 Pro", "12 Pro Max"],
            "13": ["13", "13 mini", "13 Pro", "13 Pro Max"],
            "14": ["14", "14 Plus", "14 Pro", "14 Pro Max"],
            "15": ["15", "15 Plus", "15 Pro", "15 Pro Max"],
            "16": ["16", "16E", "16 Plus", "16 Pro", "16 Pro Max"],
            "17": ["Air", "17", "17E", "17 Pro", "17 Pro Max"],
        }
        for disp in VERSION_MODELS.get(ver, []):
            c = m_counts.get(disp, 0)
            mk = _display_to_model_key(disp)
            pth = f"{path}>md>{mk}"
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, disp),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                )
            )
        return out

    # Только iPhone: у Airpods/iPad тоже есть сегмент `>md>`, но другая глубина parts (без ver/md как у iPhone).
    if path.startswith("root>cat>iPhone") and ">md>" in path and ">mem>" not in path:
        parts = path.split(">")
        ver = parts[4]
        mk = parts[6]
        var_counts = _iphone_memory_counts(iphone_new, ver, mk)
        order = ["64", "128", "256", "512", "1Tb"]
        for mem in order:
            mem_seg = mem.lower()
            pth = f"{path}>mem>{mem_seg}"
            if not editor and _tc(pth, items, btn_map) <= 0:
                continue
            label = "1Tb" if mem == "1Tb" else f"{mem}Gb"
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, label),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                )
            )
        return out

    if ">mem>" in path and ">stor>" not in path:
        parts = path.split(">")
        ver, mk, mem = parts[4], parts[6], parts[8]
        if ver in ("12", "13", "14", "15", "16"):
            return out
        stor_counts = _iphone_storage_counts(iphone_new, ver, mk, mem)
        for stor, c in stor_counts.items():
            stor_safe = stor.replace("+", "p")
            pth = f"{path}>stor>{stor_safe}"
            if not editor and _tc(pth, items, btn_map) <= 0:
                continue
            lbl = "eSim" if stor == "esim" else "(1+1)" if stor == "1+1" else "2sim"
            out.append(
                MenuNode(
                    path=pth,
                    label=effective_hardcoded_label(pth, lbl),
                    kind="hardcoded",
                    count=_tc(pth, items, btn_map),
                    hidden=pth in hidden,
                )
            )
        return out

    return out


def get_merged_menu_nodes(db: Session, parent_path: str, editor: bool) -> List[MenuNode]:
    items = load_new_products_dicts(db)
    buttons = _load_buttons(db)
    btn_map = {b.id: b for b in buttons}
    hard = _hardcoded_children(parent_path, items, btn_map, editor=editor)
    if not editor:
        hard = [n for n in hard if not n.hidden]
    custom_nodes: List[MenuNode] = []
    for b in buttons:
        if b.parent_path != parent_path:
            continue
        if getattr(b, "is_service", False):
            continue
        pth = custom_node_path(b.id)
        custom_nodes.append(
            MenuNode(
                path=pth,
                label=b.label,
                kind="custom",
                count=_tc(pth, items, btn_map),
                hidden=False,
                custom_id=b.id,
            )
        )
    custom_nodes.sort(key=lambda x: (x.label, x.custom_id or 0))
    if not editor:
        hard = [n for n in hard if n.count > 0]
        custom_nodes = [n for n in custom_nodes if n.count > 0]
    return hard + custom_nodes


def add_custom_button(db: Session, parent_path: str, label: str, user_id: int) -> NewMenuButton:
    label = (label or "").strip()[:128]
    if not label:
        raise ValueError("Пустое название")
    max_so = (
        db.query(NewMenuButton)
        .filter(NewMenuButton.parent_path == parent_path)
        .order_by(NewMenuButton.sort_order.desc())
        .first()
    )
    so = (max_so.sort_order + 1) if max_so else 0
    row = NewMenuButton(
        parent_path=parent_path,
        label=label,
        sort_order=so,
        created_by_user_id=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("menu_constructor: add button %s under %s", row.id, parent_path)
    return row


def _descendant_button_ids(db: Session, root_id: int) -> Set[int]:
    buttons = _load_buttons(db)
    idx = {b.id: b for b in buttons}

    def walk(bid: int) -> Set[int]:
        s = {bid}
        for b in buttons:
            if b.parent_path == custom_node_path(bid):
                s |= walk(b.id)
        return s

    return walk(root_id)


def delete_custom_button_cascade(db: Session, button_id: int) -> Tuple[int, int]:
    """Удаляет кнопку и потомков. Возвращает (удалено кнопок, удалено товаров)."""
    ids = _descendant_button_ids(db, button_id)
    id_list = list(ids)
    n_prod = db.query(Product).filter(Product.custom_button_id.in_(id_list)).delete(
        synchronize_session=False
    )
    n_btn = 0
    for bid in id_list:
        n_btn += db.query(NewMenuButton).filter(NewMenuButton.id == bid).delete(
            synchronize_session=False
        )
    db.commit()
    logger.info("menu_constructor: delete buttons %s, detach products %s", n_btn, n_prod)
    return n_btn, n_prod


def count_delete_preview(db: Session, button_id: int) -> Tuple[int, int]:
    ids = _descendant_button_ids(db, button_id)
    n_btn = len(ids)
    n_prod = db.query(Product).filter(Product.custom_button_id.in_(list(ids))).count()
    return n_btn, n_prod


def _normalize_vk_link(link: str) -> str:
    link = (link or "").strip()
    if not link:
        return ""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return f"https://{link}"


def _extract_vk_product_id(vk_link: str) -> Optional[int]:
    if not vk_link:
        return None
    match = re.search(r"product-\d+_(\d+)", vk_link)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _get_or_create_sync_post(db: Session) -> Optional[str]:
    post = db.query(Post).filter(Post.id == POST_ID_FOR_NEW_PRODUCTS).first()
    if post:
        return post.id
    try:
        post = Post(id=POST_ID_FOR_NEW_PRODUCTS, text="Синхронизация товаров из ВК")
        db.add(post)
        db.commit()
        return post.id
    except Exception as e:
        logger.error("sync post: %s", e)
        db.rollback()
        return None


def get_or_create_service_button(db: Session, leaf_path: str, user_id: int = 0) -> NewMenuButton:
    """Служебная кнопка-привязка на захардкоженном листе (не показывается в меню)."""
    existing = (
        db.query(NewMenuButton)
        .filter(NewMenuButton.parent_path == leaf_path, NewMenuButton.is_service.is_(True))
        .first()
    )
    if existing:
        return existing
    row = NewMenuButton(
        parent_path=leaf_path,
        label="_service_",
        sort_order=9999,
        is_service=True,
        created_by_user_id=user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("menu_constructor: service button %s under %s", row.id, leaf_path)
    return row


def attach_custom_product(
    db: Session,
    parent_path: str,
    vk_link: str,
    name: str,
    price: str,
    user_id: int,
    display_label: Optional[str] = None,
) -> Product:
    vk_link = _normalize_vk_link(vk_link)
    if not vk_link or "product-" not in vk_link:
        raise ValueError("Некорректная ссылка на товар ВК")
    name = (name or "").strip()[:512]
    if not name:
        raise ValueError("Пустое название")
    price = (price or "").strip()
    if not price.isdigit():
        raise ValueError("Цена должна быть целым числом (руб.)")
    price_str = f"{int(price)}₽"

    target_button_id: int
    if parent_path.startswith("custom:"):
        target_button_id = int(parent_path.split(":", 1)[1])
        leaf = db.query(NewMenuButton).filter(NewMenuButton.id == target_button_id).first()
        if not leaf:
            raise ValueError("Кнопка не найдена")
    elif is_hardcoded_leaf_with_products(parent_path):
        svc = get_or_create_service_button(db, parent_path, user_id)
        target_button_id = svc.id
    else:
        buttons = (
            db.query(NewMenuButton)
            .filter(
                NewMenuButton.parent_path == parent_path,
                NewMenuButton.is_service.is_(False),
            )
            .order_by(NewMenuButton.sort_order, NewMenuButton.id)
            .all()
        )
        if not buttons:
            raise ValueError(
                "Сначала создайте пользовательскую кнопку в этом разделе "
                "или откройте лист модели и добавьте товар там."
            )
        if len(buttons) > 1:
            raise ValueError(
                "Несколько пользовательских кнопок. Откройте нужную (внутрь) и добавьте товар там."
            )
        target_button_id = buttons[0].id

    post_id = _get_or_create_sync_post(db)
    if not post_id:
        raise ValueError("Не удалось создать пост-заглушку")

    vk_product_id = _extract_vk_product_id(vk_link)
    dl = (display_label or "").strip()[:128] or None
    if not dl:
        from app.utils.product_label import button_label_for_product

        dl = button_label_for_product(
            {"name": name, "price": price_str, "collection_name": CUSTOM_COLLECTION}
        )[:128] or None
    product = Product(
        post_id=post_id,
        vk_product_id=vk_product_id,
        vk_product_link=vk_link,
        name=name,
        display_label=dl,
        price=price_str,
        collection_name=CUSTOM_COLLECTION,
        custom_button_id=target_button_id,
        status="active",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    logger.info("menu_constructor: product %s user=%s button=%s", product.id, user_id, target_button_id)
    return product


def delete_custom_product(db: Session, product_id: int) -> bool:
    """Удалить кастом-товар из БД; пустую служебную кнопку тоже убрать."""
    p = db.query(Product).filter(Product.id == int(product_id)).first()
    if not p or (p.collection_name or "").strip() != CUSTOM_COLLECTION:
        return False
    bid = p.custom_button_id
    db.delete(p)
    db.flush()
    if bid:
        btn = db.query(NewMenuButton).filter(NewMenuButton.id == int(bid)).first()
        if btn and btn.is_service:
            left = db.query(Product).filter(Product.custom_button_id == btn.id).count()
            if left == 0:
                db.delete(btn)
    db.commit()
    logger.info("menu_constructor: deleted product %s", product_id)
    return True


def list_products_for_custom_leaf(db: Session, button_id: int) -> List[Dict[str, Any]]:
    rows = (
        db.query(Product)
        .filter(Product.custom_button_id == button_id, Product.collection_name == CUSTOM_COLLECTION)
        .order_by(Product.id)
        .all()
    )
    return [_product_to_dict(p) for p in rows]


def is_hardcoded_leaf_with_products(path: str) -> bool:
    if path.startswith("custom:"):
        return True
    if path.startswith("root>cat>Airpods>md>"):
        return True
    if path.startswith("root>cat>iPad>md>"):
        return True
    if "Apple Watch" in path and ">sz>" in path:
        return True
    if ">mem>" in path and ">stor>" not in path:
        parts = path.split(">")
        if len(parts) >= 9 and parts[4] in ("12", "13", "14", "15", "16"):
            return True
    if ">stor>" in path:
        return True
    return False


def _iphone_model_key_to_display(version: str, model_key: str) -> str:
    VERSION_MODELS = {
        "12": ["12", "12 mini", "12 Pro", "12 Pro Max"],
        "13": ["13", "13 mini", "13 Pro", "13 Pro Max"],
        "14": ["14", "14 Plus", "14 Pro", "14 Pro Max"],
        "15": ["15", "15 Plus", "15 Pro", "15 Pro Max"],
        "16": ["16", "16E", "16 Plus", "16 Pro", "16 Pro Max"],
        "17": ["Air", "17", "17E", "17 Pro", "17 Pro Max"],
    }
    for disp in VERSION_MODELS.get(version, []):
        if _display_to_model_key(disp) == model_key:
            return disp
    return (model_key or "").replace("_", " ")


def _mem_segment_to_label(mem_seg: str) -> str:
    s = (mem_seg or "").lower()
    if s in ("1tb", "1_tb"):
        return "1 ТБ"
    if mem_seg.isdigit():
        return f"{mem_seg} ГБ"
    return mem_seg


def _stor_segment_to_label(stor_seg: str) -> str:
    sk = stor_seg.replace("p", "+")
    return {"esim": "eSim", "1+1": "(1+1)", "2sim": "2 SIM"}.get(sk, sk)


def human_constructor_breadcrumb(path: str, db: Optional[Session] = None) -> str:
    """Краткий человекочитаемый путь для экрана редактора (без HTML)."""
    path = (path or "").strip()
    if path == "root":
        return "Корень — категории"
    if path.startswith("custom:"):
        try:
            bid = int(path.split(":", 1)[1])
        except ValueError:
            return path
        if db is None:
            return f"Пользовательская кнопка ({path})"
        btn = db.query(NewMenuButton).filter(NewMenuButton.id == bid).first()
        lbl = (btn.label if btn else "") or "кнопка"
        parent = (btn.parent_path if btn else None) or "root"
        if parent == "root":
            return f"«{lbl}»"
        return f"{human_constructor_breadcrumb(parent, db)} → «{lbl}»"
    parts = path.split(">")
    if len(parts) < 3 or parts[0] != "root" or parts[1] != "cat":
        return path.replace(">", " → ")
    cat = parts[2]
    bits: List[str] = ["Список новых", cat]
    if cat == "Airpods":
        if len(parts) >= 5 and parts[3] == "md":
            mk = parts[4]
            inv = {v: k for k, v in AIRPODS_KEY.items()}
            bits.append(inv.get(mk, mk))
    elif cat == "iPad":
        if len(parts) >= 5 and parts[3] == "md":
            mk = parts[4]
            inv = {v: k for k, v in IPAD_KEY.items()}
            bits.append(inv.get(mk, mk))
    elif cat == "Apple Watch":
        if len(parts) >= 5 and parts[3] == "wc":
            ck = parts[4]
            inv = {v: k for k, v in WATCH_KEY.items()}
            cat_watch = inv.get(ck, ck)
            bits.append(f"AW {cat_watch}")
        if len(parts) >= 7 and parts[5] == "sz":
            bits.append(parts[6].replace("_", " "))
    elif cat == "iPhone":
        if len(parts) > 4 and parts[3] == "ver":
            ver = parts[4]
            bits.append(f"iPhone {ver}")
            if len(parts) > 6 and parts[5] == "md":
                mk = parts[6]
                bits.append(_iphone_model_key_to_display(ver, mk))
            if len(parts) > 8 and parts[7] == "mem":
                bits.append(_mem_segment_to_label(parts[8]))
            if len(parts) > 10 and parts[9] == "stor":
                bits.append(_stor_segment_to_label(parts[10]))
    return " → ".join(bits)


def _mc_product_row_tag(p: dict) -> str:
    cn = (p.get("collection_name") or "").strip()
    if cn == CUSTOM_COLLECTION:
        return "свой товар"
    return "стандартная"


def collect_leaf_products(db: Session, path: str) -> List[Dict[str, Any]]:
    """Классические + custom товары на листе меню (с дедупликацией)."""
    if path.startswith("custom:"):
        try:
            bid = int(path.split(":", 1)[1])
        except ValueError:
            return []
        return list_products_for_custom_leaf(db, bid)

    if not is_hardcoded_leaf_with_products(path):
        return []

    parts = path.split(">")
    items = load_new_products_dicts(db)
    plist: List[dict] = []

    if path.startswith("root>cat>iPhone") and ">stor>" in path and len(parts) >= 11:
        version, model_key, mem_key, stor_key = parts[4], parts[6], parts[8], parts[10]
        iphone_new = _filter_classical(items, "iPhone новые")
        plist = list(_iphone_products_for_storage(iphone_new, version, model_key, mem_key, stor_key))
    elif (
        path.startswith("root>cat>iPhone")
        and len(parts) >= 9
        and parts[3] == "ver"
        and parts[5] == "md"
        and parts[7] == "mem"
        and ">stor>" not in path
        and parts[4] in ("12", "13", "14", "15", "16")
    ):
        version, model_key, mem_key = parts[4], parts[6], parts[8]
        memory_norm = "1Tb" if mem_key.lower() == "1tb" else mem_key
        iphone_new = _filter_classical(items, "iPhone новые")
        plist = list(_iphone_products_for_memory(iphone_new, version, model_key, memory_norm))
    elif len(parts) == 5 and parts[:4] == ["root", "cat", "Airpods", "md"]:
        mk = parts[4]
        inv = {v: k for k, v in AIRPODS_KEY.items()}
        model_name = inv.get(mk)
        if not model_name:
            return []
        for p in _filter_classical(items, "Airpods"):
            if _parse_airpods_model(p.get("name", "")) == model_name:
                plist.append(p)
    elif len(parts) == 5 and parts[:4] == ["root", "cat", "iPad", "md"]:
        mk = parts[4]
        inv = {v: k for k, v in IPAD_KEY.items()}
        model_name = inv.get(mk)
        if not model_name:
            return []
        for p in _filter_classical(items, "iPad"):
            if _parse_ipad_model(p.get("name", "")) == model_name:
                plist.append(p)
    elif "Apple Watch" in path and ">sz>" in path and len(parts) >= 7 and parts[5] == "sz":
        ck, szk = parts[4], parts[6]
        inv = {v: k for k, v in WATCH_KEY.items()}
        cat_watch = inv.get(ck)
        if not cat_watch:
            return []
        for p in _filter_classical(items, "Apple Watch"):
            if _parse_apple_watch_category(p.get("name", "")) != cat_watch:
                continue
            sz = _parse_apple_watch_size(p.get("name", ""))
            if sz and sz.replace(" ", "").lower() == szk.replace("_", ""):
                plist.append(p)
    else:
        return []

    seen: Set[int] = {int(p["id"]) for p in plist if p.get("id") is not None}
    for p in list_custom_products_for_path(db, path):
        pid = p.get("id")
        if pid is None or int(pid) in seen:
            continue
        plist.append(p)
        seen.add(int(pid))
    return plist


def constructor_leaf_inline_like_list_new(
    db: Session, path: str
) -> List[Tuple[str, str]]:
    """Подписи inline-кнопок на листьях (без цены)."""
    out: List[Tuple[str, str]] = []
    for p in collect_leaf_products(db, path):
        lbl = button_label_for_product(p)
        out.append((lbl, _mc_product_row_tag(p)))
    return out


def format_constructor_nodes_summary_html(
    db: Session, path: str, nodes: List[MenuNode], max_items: int = 40
) -> str:
    """Подменю редактора + те же подписи inline-кнопок, что в «Список новых» на листьях."""
    lines: List[str] = []
    leaf_like = constructor_leaf_inline_like_list_new(db, path)
    if not nodes and not leaf_like:
        return (
            "<i>Подменю нет — можно добавить свою кнопку или товар "
            "(если в разделе одна своя кнопка-лист, товар добавляется внутри неё).</i>"
        )
    max_leaf = 35
    for n in nodes[:max_items]:
        prefix = "🚫 " if n.hidden and n.kind == "hardcoded" else ""
        em = (n.emoji or "").strip()
        em_html = f"{em} " if em else ""
        kind_ru = "своя кнопка" if n.kind == "custom" else "стандартная"
        lines.append(
            f"• {prefix}{em_html}<b>{escape(n.label)}</b> ({n.count}) · {kind_ru}"
        )
    if len(nodes) > max_items:
        lines.append(f"<i>… и ещё {len(nodes) - max_items} в подменю</i>")
    if leaf_like:
        if lines:
            lines.append("")
        custom_menu_labels = {n.label.strip() for n in nodes if n.kind == "custom"}
        shown = leaf_like[:max_leaf]
        for lbl, tag in shown:
            if tag == "свой товар" and lbl.strip() in custom_menu_labels:
                continue
            lines.append(f"• <b>{escape(lbl)}</b> (1) · {escape(tag)}")
        if len(leaf_like) > max_leaf:
            lines.append(f"<i>… и ещё {len(leaf_like) - max_leaf} позиций</i>")
    return "\n".join(lines) if lines else (
        "<i>Подменю нет — можно добавить свою кнопку или товар.</i>"
    )


def list_constructor_preview_product_names(db: Session, path: str, limit: int = 12) -> List[str]:
    """Краткие подписи товаров на листьях (без цены)."""
    out: List[str] = []
    for p in collect_leaf_products(db, path):
        lbl = button_label_for_product(p)
        if lbl:
            out.append(lbl)
        if len(out) >= limit:
            break
    return out


def format_constructor_editor_message_html(path: str, nodes: List[MenuNode], db: Session) -> str:
    """Текст информационного сообщения редактора: путь + подменю + товары."""
    human = escape(human_constructor_breadcrumb(path, db))
    tech = escape(path if path.startswith("custom:") else path.replace(">", " → "))
    nodes_block = format_constructor_nodes_summary_html(db, path, nodes)
    products = list_constructor_preview_product_names(db, path, limit=10)
    prod_block = ""
    if products:
        prod_lines = "\n".join(f"• {escape(n[:200])}" for n in products)
        more_check = list_constructor_preview_product_names(db, path, limit=11)
        more = "\n<i>… показаны не все товары</i>" if len(more_check) > len(products) else ""
        prod_block = f"\n\n<b>Товары в этом узле:</b>\n{prod_lines}{more}"
    hint = (
        "Строка слева — переход в подменю. ⚙️ — скрыть/показать или своя подпись (стандарт), "
        "удалить кнопку (своя), 🗑 — удалить свой товар."
    )
    return (
        f"🧩 <b>Редактор меню «Список новых»</b>\n\n"
        f"📍 <b>Сейчас:</b> {human}\n"
        f"<code>{tech}</code>\n\n"
        f"<b>Подменю и кнопки здесь:</b>\n{nodes_block}"
        f"{prod_block}\n\n{hint}"
    )


def get_new_menu_button(db: Session, button_id: int) -> Optional[NewMenuButton]:
    return db.query(NewMenuButton).filter(NewMenuButton.id == button_id).first()


def get_custom_extra_entries(db: Session, parent_path: str) -> List[Dict[str, str]]:
    """Текст и callback для дополнительных пользовательских кнопок на экране parent_path."""
    nodes = get_merged_menu_nodes(db, parent_path, editor=False)
    extras = [
        {"text": f"{n.label} ({n.count})", "callback": f"new_custom_{n.custom_id}"}
        for n in nodes
        if n.kind == "custom" and n.custom_id is not None
    ]
    return extras


def back_callback_for_custom_parent(parent_path: str) -> str:
    if parent_path == "root":
        return "new_products_menu"
    if parent_path.startswith("custom:"):
        return f"new_custom_{int(parent_path.split(':', 1)[1])}"
    return "new_products_menu"


def list_custom_products_for_path(db: Session, path: str) -> List[Dict[str, Any]]:
    """Возвращает custom-товары, относящиеся к конкретному path (по цепочке custom_button_id -> parent_path)."""
    buttons = _load_buttons(db)
    btn_map = {b.id: b for b in buttons}
    rows = (
        db.query(Product)
        .filter(Product.collection_name == CUSTOM_COLLECTION, Product.custom_button_id.isnot(None))
        .all()
    )
    out: List[Dict[str, Any]] = []
    for p in rows:
        bid = p.custom_button_id
        if not bid:
            continue
        try:
            paths = _paths_for_product_button(int(bid), btn_map)
        except Exception:
            continue
        if path in paths:
            out.append(_product_to_dict(p))
    return out
