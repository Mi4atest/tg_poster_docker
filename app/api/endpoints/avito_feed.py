"""Публичный XML-фид для автозагрузки Авито."""
from fastapi import APIRouter, Response

from app.integrations.avito.feed_store import load_feed_xml

router = APIRouter()


@router.get("/feeds/avito.xml")
def get_avito_autoload_feed():
    xml = load_feed_xml()
    if not xml:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Ads formatVersion="3" target="Avito.ru"></Ads>\n'
        )
    return Response(content=xml, media_type="application/xml; charset=utf-8")
