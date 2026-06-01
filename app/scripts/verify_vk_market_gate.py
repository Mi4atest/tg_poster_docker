"""
Проверка гипотез H1–H3 без публикации в VK (нет вызовов VK API).

Сравнивает:
  - VK_MARKET_ENABLED из окружения;
  - features.vk_market_enabled из БД;
  - фактическое решение воркера: SettingsService.is_vk_market_publish_allowed() (env AND БД).

Запуск из корня проекта (как у других скриптов):
  python -m app.scripts.verify_vk_market_gate

С Docker:
  docker-compose exec app python -m app.scripts.verify_vk_market_gate

Чтобы зафиксировать H3: в боте выключите «Товары ВК» (это не публикация и не спам),
затем запустите скрипт — в логе будет settings_vk_market=false при том же .env.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from app.config.settings import VK_MARKET_ENABLED
    from app.services.settings_service import get_settings_service

    env_on = bool(VK_MARKET_ENABLED)
    try:
        settings_on = bool(get_settings_service().is_vk_market_enabled())
    except Exception as e:
        settings_on = None  # type: ignore[assignment]
        settings_err = repr(e)
    else:
        settings_err = None

    try:
        current_schedules = bool(get_settings_service().is_vk_market_publish_allowed())
    except Exception:
        current_schedules = env_on
    combined_gate = env_on and bool(settings_on) if settings_on is not None else None

    report = {
        "env_vk_market": env_on,
        "settings_vk_market": settings_on,
        "settings_read_error": settings_err,
        "H1_current_branch_schedules_product_task": current_schedules,
        "H2_product_path_would_run_if_task_created": current_schedules,
        "H3_settings_false_but_branch_true": (
            settings_on is False and env_on is True and current_schedules is True
        ),
        "combined_env_and_settings_would_be": combined_gate,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        "\nИнтерпретация:\n"
        "- H1_current_branch… = is_vk_market_publish_allowed() (env и переключатель в БД).\n"
        "- H2: совпадает с H1 после фикса.\n"
        "- H3: true только при баге (в БД выкл, env вкл, но ветка всё ещё true); после фикса должно быть false.\n",
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
