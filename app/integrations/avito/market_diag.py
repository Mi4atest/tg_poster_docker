"""Коды отказов оценки рынка: логи для grep и тексты для продавца."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

CODE_INTERVAL = "interval"
CODE_DAILY_LIMIT = "daily_limit"
CODE_NO_PROXY = "no_proxy"
CODE_HTTP_439 = "http_439"
CODE_HTTP_403 = "http_403"
CODE_HTTP_429 = "http_429"
CODE_CAPTCHA = "captcha"
CODE_PARSE = "parse_error"
CODE_AVITO_BLOCK = "avito_block"
CODE_SPFA = "spfa"
CODE_TRANSPORT = "transport"
CODE_FAIL = "fail"
CODE_UNAVAILABLE = "unavailable"
CODE_LIVE = "live"

KNOWN_CODES = frozenset(
    {
        CODE_INTERVAL,
        CODE_DAILY_LIMIT,
        CODE_NO_PROXY,
        CODE_HTTP_439,
        CODE_HTTP_403,
        CODE_HTTP_429,
        CODE_CAPTCHA,
        CODE_PARSE,
        CODE_AVITO_BLOCK,
        CODE_SPFA,
        CODE_TRANSPORT,
        CODE_FAIL,
        CODE_UNAVAILABLE,
        CODE_LIVE,
    }
)

_MINS_RE = re.compile(r"~(\d+)\s*мин")
_SEC_RE = re.compile(r"(\d+)\s*сек")


def log_market(event: str, code: str, **fields: Any) -> None:
    """Одна строка: Avito market {event} code=... k=v — удобно грепать."""
    parts = [f"Avito market {event} code={code}"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = int(value)
        text = str(value).replace(" ", "_")
        if len(text) > 160:
            text = text[:157] + "..."
        parts.append(f"{key}={text}")
    line = " ".join(parts)
    if event in {"fail", "block"}:
        logger.warning(line)
    else:
        logger.info(line)


def user_notice(
    code: str,
    *,
    wait_sec: Optional[int] = None,
    wait_mins: Optional[int] = None,
    daily_limit: Optional[int] = None,
    has_proxy: Optional[bool] = None,
) -> str:
    """Живой, но понятный текст для чата. Без SPFA/HTTP-жаргона, кроме «прокси»."""
    if has_proxy is False and code in {
        CODE_HTTP_439,
        CODE_HTTP_403,
        CODE_AVITO_BLOCK,
        CODE_CAPTCHA,
        CODE_TRANSPORT,
    }:
        code = CODE_NO_PROXY

    if code == CODE_INTERVAL:
        wait = max(1, int(wait_sec or 1))
        return (
            f"Не спешим: следующий свежий поиск через {wait} сек. "
            "Так Avito реже принимает нас за робота. "
            "Готовую оценку этой модели можно открыть в «Последних отчётах»."
        )
    if code == CODE_DAILY_LIMIT:
        limit = int(daily_limit or 40)
        return (
            f"На сегодня свежих поисков хватит — лимит {limit}. "
            "Завтра можно обновить снова. Уже посчитанные модели открываются из истории без лимита."
        )
    if code == CODE_NO_PROXY:
        return (
            "Avito не отдал выдачу: не задан российский прокси. "
            "Откройте Настройки → Оценка рынка Avito и укажите "
            "login:password@host:port — без этого Avito почти всегда отказывает."
        )
    if code == CODE_HTTP_439:
        when = (
            f" Обычно отпускает через ~{wait_mins} мин."
            if wait_mins
            else " Обычно отпускает примерно через час."
        )
        return (
            "Avito не пустил запрос — сработала защита от роботов."
            + when
            + " Если оценка этой модели уже была, она осталась в истории."
        )
    if code == CODE_HTTP_429:
        when = f" через ~{wait_mins} мин" if wait_mins else " чуть позже"
        return (
            f"Avito просит подождать: слишком много запросов подряд. "
            f"Поставим живой поиск на паузу{when}."
        )
    if code == CODE_HTTP_403:
        when = f" через ~{wait_mins} мин" if wait_mins else " чуть позже"
        return (
            f"Avito закрыл доступ к выдаче. Попробуйте{when} — "
            "часто проходит само. Старый отчёт, если был, можно открыть из истории."
        )
    if code == CODE_CAPTCHA:
        when = f" через ~{wait_mins} мин" if wait_mins else " чуть позже"
        return (
            "Avito попросил подтвердить, что запрос не от робота. "
            f"Свежий поиск поставим на паузу{when}. "
            "Сохранённая оценка никуда не денется."
        )
    if code == CODE_PARSE:
        return (
            "Avito ответил, но в выдаче не видно объявлений. "
            "Иногда так бывает при сбое страницы — повторите позже "
            "или попробуйте другую модель и память."
        )
    if code == CODE_SPFA:
        return (
            "Не получилось подготовить запрос к Avito (cookies). "
            "Проверьте в настройках ключ SPFA и что он ещё с балансом — "
            "затем повторите поиск."
        )
    if code == CODE_TRANSPORT:
        when = f" через ~{wait_mins} мин" if wait_mins else " через пару минут"
        return (
            f"До Avito сейчас не достучались (сеть или прокси). "
            f"Проверьте прокси в настройках и повторите{when}."
        )
    if code == CODE_AVITO_BLOCK:
        when = f" через ~{wait_mins} мин" if wait_mins else " через час"
        return (
            f"Avito временно не отдаёт выдачу. Подождите{when}. "
            "Готовый отчёт по этой модели, если он уже был, смотрите в истории."
        )
    if code == CODE_FAIL:
        when = f" через ~{wait_mins} мин" if wait_mins else " чуть позже"
        return (
            f"Оценка сейчас не обновилась. Попробуйте{when}. "
            "Если отчёт уже сохраняли — он в «Последних отчётах»."
        )
    return (
        "Сейчас свежую оценку получить не вышло. "
        "Загляните через несколько минут или откройте сохранённый отчёт из истории."
    )


def infer_market_code(reason: str) -> str:
    raw = (reason or "").strip()
    if raw in KNOWN_CODES:
        return raw
    text = raw.lower()
    if "http 439" in text or "http_439" in text:
        return CODE_HTTP_439
    if "http 429" in text or "http_429" in text:
        return CODE_HTTP_429
    if "http 403" in text or "http_403" in text:
        return CODE_HTTP_403
    if "прокси" in text and (
        "не задан" in text or "нет " in text or "без " in text or "нужен" in text
    ):
        return CODE_NO_PROXY
    if "captcha" in text or "не робот" in text or "подтвердить" in text:
        return CODE_CAPTCHA
    if "карточек" in text or "не найден" in text or "parse" in text:
        return CODE_PARSE
    if "cookie" in text or "spfa" in text:
        return CODE_SPFA
    if "curl_cffi" in text or "сеть" in text or "не удалось получить" in text:
        return CODE_TRANSPORT
    if "суточн" in text or ("лимит" in text and "свеж" in text):
        return CODE_DAILY_LIMIT
    if "подождите ещё" in text or "между новыми" in text or "следующий свежий" in text:
        return CODE_INTERVAL
    if "не пустил" in text or "защита от роботов" in text:
        return CODE_HTTP_439
    if "ограничил" in text or "робот" in text:
        return CODE_AVITO_BLOCK
    if "не удалось" in text or "не обновилась" in text or "приостанов" in text:
        return CODE_FAIL
    return CODE_UNAVAILABLE


def extract_wait_mins(reason: str) -> Optional[int]:
    match = _MINS_RE.search(reason or "")
    if match:
        return max(1, int(match.group(1)))
    return None


def extract_wait_sec(reason: str) -> Optional[int]:
    match = _SEC_RE.search(reason or "")
    if match:
        return max(1, int(match.group(1)))
    return None


def user_facing_market_error(reason: str) -> str:
    """Простые формулировки для продавца. Коды и старые фразы сводим к одному тексту."""
    text = (reason or "").strip()
    if not text:
        return user_notice(CODE_UNAVAILABLE)
    code = infer_market_code(text)
    return user_notice(
        code,
        wait_sec=extract_wait_sec(text),
        wait_mins=extract_wait_mins(text),
    )


def code_from_http_status(status: int) -> str:
    if status == 439:
        return CODE_HTTP_439
    if status == 429:
        return CODE_HTTP_429
    if status == 403:
        return CODE_HTTP_403
    return CODE_AVITO_BLOCK
