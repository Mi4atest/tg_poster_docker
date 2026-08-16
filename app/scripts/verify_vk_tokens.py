"""Проверка VK_ACCESS_TOKEN (сообщество) и VK_MARKET_ACCESS_TOKEN (market.*)."""
import sys

from app.config.settings import VK_APP_ID, VK_MARKET_ACCESS_TOKEN


def _normalize_market_token(raw: str) -> str:
    t = (raw or "").strip()
    while t.startswith("VK_MARKET_ACCESS_TOKEN="):
        t = t.split("=", 1)[1].strip()
    return t


def _validate_market_token_format(token: str) -> list[str]:
    issues = []
    if not token:
        issues.append("VK_MARKET_ACCESS_TOKEN пуст")
        return issues
    if "VK_MARKET_ACCESS_TOKEN" in token or "ACCESS_TOKEN" in token[:40]:
        issues.append("в .env попал текст «VK_MARKET_ACCESS_TOKEN=» — оставьте только значение токена")
    if token.startswith("vk2.a."):
        issues.append(
            "токен vk2.a.* из VK ID не поддерживает market.* / wall (ошибки 1051/15). "
            "Нужен user token формата vk1.a.* с правом market (см. /vk/oauth/help)"
        )
    elif not token.startswith("vk1.a."):
        issues.append("ожидается user token vk1.a.* для api.vk.ru (market, wall от пользователя)")
    return issues
from app.utils.vk_client import (
    community_token,
    get_community_vk_session,
    get_market_vk_session,
    market_token,
    market_token_source,
    resolved_vk_group_id_int,
    vk_api_error_code,
)


def _check(label: str, fn) -> bool:
    try:
        fn()
        print(f"OK  {label}")
        return True
    except Exception as e:
        code = vk_api_error_code(e)
        print(f"FAIL {label}: [{code}] {e}")
        return False


def main() -> int:
    gid = resolved_vk_group_id_int()
    owner = -gid
    print(f"group_id={gid} market_token_source={market_token_source()}")
    print(f"community_token_set={bool(community_token())} market_token_set={bool(market_token())}")
    print(f"VK_MARKET_ACCESS_TOKEN dedicated={bool(VK_MARKET_ACCESS_TOKEN)}")
    if VK_APP_ID:
        print(f"VK_APP_ID={VK_APP_ID}")

    mt = _normalize_market_token(VK_MARKET_ACCESS_TOKEN or market_token())
    for msg in _validate_market_token_format(mt):
        print(f"WARN  {msg}")

    cvk = get_community_vk_session().get_api()
    mvk = get_market_vk_session().get_api()

    ok = True
    ok &= _check("community groups.getById", lambda: cvk.groups.getById(group_id=gid))
    if mt.startswith("vk2.a."):
        print("SKIP  market.* (vk2.a VK ID token incompatible with VK API market methods)")
        ok = False
    else:
        ok &= _check(
            "market market.get (user vk1.a + market scope)",
            lambda: mvk.market.get(owner_id=owner, count=1),
        )
    print("\n--- Справка ---")
    print("[27]  = ключ сообщества не подходит для market → нужен user vk1.a в VK_MARKET_ACCESS_TOKEN")
    print("[1051]/[15] = токен vk2.a (VK ID) не подходит для market → нужен vk1.a, не VK ID OAuth")
    print("Ключ группы → VK_ACCESS_TOKEN | User vk1.a с market → VK_MARKET_ACCESS_TOKEN")
    print("https://appleshop.ap43.ru/vk/oauth/help")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
