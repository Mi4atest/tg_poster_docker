"""Парсинг ссылок на сообщения VK-канала."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class VkChannelMessageRef:
    peer_id: int
    cmid: int


_CHANNEL_PATH_RE = re.compile(
    r"/im/channels/(?P<peer>-?\d+)",
    re.IGNORECASE,
)
_BARE_RE = re.compile(
    r"^\s*(?P<peer>-?\d+)\s*[,;\s]+(?P<cmid>\d+)\s*$",
)


def parse_vk_channel_message_link(text: str) -> Optional[VkChannelMessageRef]:
    """
    Разбирает ссылку вида:
      https://vk.ru/im/channels/-235526445?cmid=1
      https://vk.com/im/channels/-235526445?cmid=1  (legacy, тоже принимается)
    или короткую запись: ``-235526445,1`` / ``-235526445 1``.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    bare = _BARE_RE.match(raw)
    if bare:
        return VkChannelMessageRef(
            peer_id=int(bare.group("peer")),
            cmid=int(bare.group("cmid")),
        )

    # Может прийти несколько строк — берём первую валидную URL-строку
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = urlparse(line if "://" in line else f"https://{line}")
        path_m = _CHANNEL_PATH_RE.search(parsed.path or "")
        if not path_m:
            # иногда peer в query
            continue
        peer_id = int(path_m.group("peer"))
        qs = parse_qs(parsed.query or "")
        cmid_vals = qs.get("cmid") or qs.get("conversation_message_id") or []
        if not cmid_vals:
            continue
        try:
            cmid = int(str(cmid_vals[0]).strip())
        except ValueError:
            continue
        return VkChannelMessageRef(peer_id=peer_id, cmid=cmid)
    return None


def parse_vk_channel_message_links(text: str) -> List[VkChannelMessageRef]:
    """Несколько ссылок/строк → список ref (для multi-message прайса)."""
    raw = (text or "").strip()
    if not raw:
        return []
    refs: List[VkChannelMessageRef] = []
    seen = set()
    for line in raw.replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        # comma-separated bare pairs on one line
        if "," in line and "://" not in line and "channels" not in line:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if len(parts) >= 2 and parts[0].lstrip("-").isdigit():
                # either "peer,cmid" or "peer,cmid1,cmid2"
                try:
                    peer = int(parts[0])
                except ValueError:
                    continue
                for c in parts[1:]:
                    if not c.isdigit():
                        continue
                    ref = VkChannelMessageRef(peer_id=peer, cmid=int(c))
                    key = (ref.peer_id, ref.cmid)
                    if key not in seen:
                        seen.add(key)
                        refs.append(ref)
                continue
        one = parse_vk_channel_message_link(line)
        if one:
            key = (one.peer_id, one.cmid)
            if key not in seen:
                seen.add(key)
                refs.append(one)
    return refs
