from app.services.settings_service import get_settings_service


def get_platform_status_hint_text() -> str:
    service = get_settings_service()
    lines = service.get_publication_status_lines()
    return "📡 Текущие настройки публикации:\n" + "\n".join(lines)
