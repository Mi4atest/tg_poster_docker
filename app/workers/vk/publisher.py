import vk_api
import logging
import asyncio
import aiohttp
import requests
import os
import time
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config.settings import (
    API_HOST,
    API_PORT,
    VK_WALL_ATTACH_MARKET,
)
from app.utils.vk_client import get_community_vk_session, resolved_vk_group_id_int
from app.db.database import SessionLocal
from app.db.post_queries import (
    fetch_post,
    fetch_product_row_by_post_id,
    insert_publication_log,
    mark_post_published_vk,
)
from app.utils.text_formatter import format_for_vk
from app.workers.vk.upload_retry import (
    VK_WALL_UPLOAD_ATTEMPTS,
    vk_upload_backoff_seconds,
)

logger = logging.getLogger(__name__)


def _vk_upload_strict_mode() -> bool:
    try:
        from app.services.settings_service import get_settings_service

        return get_settings_service().is_vk_upload_strict_mode()
    except Exception:
        from app.config.settings import VK_UPLOAD_STRICT_MODE

        return bool(VK_UPLOAD_STRICT_MODE)


def _vk_wall_requires_market() -> bool:
    try:
        from app.services.settings_service import get_settings_service

        return get_settings_service().is_vk_wall_requires_market()
    except Exception:
        from app.config.settings import VK_WALL_REQUIRES_MARKET

        return bool(VK_WALL_REQUIRES_MARKET)


class VKPublisher:
    """Class for publishing posts to VK."""

    def __init__(self):
        """Initialize VK API session."""
        self.group_id = resolved_vk_group_id_int()
        self.vk_session = get_community_vk_session()
        self.vk = self.vk_session.get_api()
        self.upload = vk_api.VkUpload(self.vk_session)

    async def download_telegram_file(self, file_id):
        """Download file from Telegram by file_id."""
        # Create a bot instance to get file info
        bot = None
        try:
            from app.config.settings import TELEGRAM_BOT_TOKEN
            from aiogram import Bot
            bot = Bot(token=TELEGRAM_BOT_TOKEN)

            # Get file info directly from Telegram
            file_info = await bot.get_file(file_id)
            file_path = file_info.file_path

            # Get direct file URL
            file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"

            # Download file with SSL verification disabled
            async with aiohttp.ClientSession() as session:
                try:
                    # Create a custom SSL context that doesn't verify certificates
                    import ssl
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE

                    async with session.get(file_url, ssl=ssl_context) as response:
                        if response.status == 200:
                            return await response.read()
                        logger.error(f"Failed to download file from Telegram: {response.status}")
                        return None
                except Exception as e:
                    logger.error(f"Error downloading file from Telegram: {str(e)}")
                    return None
        except Exception as e:
            logger.error(f"Error downloading file {file_id} from Telegram: {str(e)}")

            # Fallback to our API endpoint
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"http://{API_HOST}:{API_PORT}/api/telegram/file/{file_id}"

                    # Try to download directly from our API
                    try:
                        async with session.get(url) as response:
                            if response.status == 200:
                                return await response.read()
                            logger.error(f"Failed to download file from API: {response.status}")
                    except Exception as e2:
                        logger.error(f"Error connecting to API: {str(e2)}")

                    # If that fails, try a different approach - download directly from Telegram
                    # but save to a temporary file first
                    try:
                        import tempfile
                        import os

                        # Create a temporary file
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
                            temp_path = temp_file.name

                        # Use curl to download the file (curl handles SSL issues better)
                        import subprocess
                        curl_cmd = [
                            "curl",
                            "-s",
                            "-k",  # Skip SSL verification
                            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
                            "-o", temp_path
                        ]

                        process = subprocess.run(curl_cmd, capture_output=True)

                        if process.returncode == 0:
                            # Read the file
                            with open(temp_path, "rb") as f:
                                content = f.read()

                            # Clean up
                            os.unlink(temp_path)

                            return content
                    except Exception as e3:
                        logger.error(f"Error using curl fallback: {str(e3)}")

                # If all methods fail
                return None
            except Exception as e2:
                logger.error(f"Error in fallback methods for file {file_id}: {str(e2)}")
                return None
        finally:
            if bot:
                await bot.session.close()

    async def _download_telegram_file_with_retry(self, file_id, attempts: int = 3):
        """Повторить временно неудачное скачивание файла из Telegram."""
        for attempt in range(1, attempts + 1):
            data = await self.download_telegram_file(file_id)
            if data:
                return data
            if attempt < attempts:
                logger.warning(
                    "Telegram media download failed (attempt %s/%s), retrying",
                    attempt,
                    attempts,
                )
                await asyncio.sleep(attempt)
        return None

    @staticmethod
    def _is_retryable_upload_error(exc: BaseException) -> bool:
        code = getattr(exc, "code", None)
        message = str(exc).lower()
        return (
            isinstance(exc, requests.RequestException)
            or code in {6, 8, 9, 10, 29}
            or (code == 100 and "photo is undefined" in message)
            or "timeout" in message
            or "temporar" in message
            or "connection" in message
        )

    def _upload_photo_sync(self, temp_file: str):
        """Синхронная загрузка фото на стену VK (вызывать через asyncio.to_thread)."""
        try:
            return self.upload.photo_wall(temp_file, group_id=self.group_id)
        except Exception as e:
            logger.error(f"Error using photo_wall: {str(e)}")
            try:
                albums = self.vk.photos.getAlbums(owner_id=-self.group_id)
                album_id = None
                for album in albums.get("items", []):
                    if album.get("title") == "Wall Photos":
                        album_id = album.get("id")
                        break
                if not album_id:
                    album = self.vk.photos.createAlbum(
                        title="Wall Photos",
                        group_id=self.group_id,
                        description="Photos for wall posts",
                    )
                    album_id = album.get("id")
                return self.upload.photo(
                    temp_file,
                    album_id=album_id,
                    group_id=self.group_id,
                )
            except Exception as e2:
                logger.error(f"Error with fallback photo upload: {str(e2)}")
                upload_server = self.vk.photos.getWallUploadServer(group_id=self.group_id)
                with open(temp_file, "rb") as f:
                    upload_response = requests.post(
                        upload_server["upload_url"],
                        files={"photo": f},
                        timeout=(10, 120),
                    )
                    upload_response.raise_for_status()
                    response = upload_response.json()
                return self.vk.photos.saveWallPhoto(
                    group_id=self.group_id,
                    photo=response["photo"],
                    server=response["server"],
                    hash=response["hash"],
                )

    async def _upload_photo_with_retry(
        self, temp_file: str, attempts: int = VK_WALL_UPLOAD_ATTEMPTS
    ):
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.to_thread(self._upload_photo_sync, temp_file)
            except Exception as exc:
                if attempt >= attempts or not self._is_retryable_upload_error(exc):
                    raise
                delay = vk_upload_backoff_seconds(attempt, exc)
                logger.warning(
                    "VK wall photo upload failed (attempt %s/%s, code=%s), retrying in %ss",
                    attempt,
                    attempts,
                    getattr(exc, "code", None),
                    delay,
                )
                await asyncio.sleep(delay)

    def _upload_video_sync(self, temp_file: str, name: str, description: str):
        return self.upload.video(
            video_file=temp_file,
            name=name,
            description=description,
            group_id=self.group_id,
        )

    async def _upload_video_with_retry(
        self,
        temp_file: str,
        name: str,
        description: str,
        attempts: int = VK_WALL_UPLOAD_ATTEMPTS,
    ):
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.to_thread(
                    self._upload_video_sync, temp_file, name, description
                )
            except Exception as exc:
                if attempt >= attempts or not self._is_retryable_upload_error(exc):
                    raise
                delay = vk_upload_backoff_seconds(attempt, exc)
                logger.warning(
                    "VK wall video upload failed (attempt %s/%s, code=%s), retrying in %ss",
                    attempt,
                    attempts,
                    getattr(exc, "code", None),
                    delay,
                )
                await asyncio.sleep(delay)

    def _wall_post_sync(self, text: str, attachments: str):
        return self.vk.wall.post(
            owner_id=-self.group_id,
            from_group=1,
            message=text,
            attachments=attachments,
        )

    async def publish_post(self, post_id, signature_enabled: bool = True):
        """Publish a post to VK."""
        db = SessionLocal()
        try:

            post = fetch_post(db, post_id)


            if not post:
                logger.error(f"Post {post_id} not found")
                return False

            # Log if already published, but continue with republishing
            if post.is_published_vk:
                logger.info(f"Post {post_id} already published to VK, republishing")

            formatted_text = format_for_vk(post.text, signature_enabled=signature_enabled)

            # Download and upload photos
            photo_attachments = []
            failed_photo_ids: list[str] = []
            failed_video_ids: list[str] = []
            total_photos = len(post.photos or [])
            total_videos = len(post.videos or [])

            for file_id in post.photos or []:
                attachments_before = len(photo_attachments)
                temp_file = None
                try:
                    # Download photo from Telegram
                    photo_data = await self._download_telegram_file_with_retry(file_id)

                    if not photo_data:
                        logger.error(f"Failed to download photo {file_id}")
                        failed_photo_ids.append(file_id)
                        continue

                    # Save photo to temporary file
                    temp_file = f"/tmp/{file_id}.jpg"
                    with open(temp_file, "wb") as f:
                        f.write(photo_data)

                    # Upload photo to VK wall (в отдельном потоке — не блокировать бота)
                    upload_result = await self._upload_photo_with_retry(temp_file)

                    # Format attachment string
                    for photo in upload_result:
                        owner_id = photo["owner_id"]
                        photo_id = photo["id"]
                        photo_attachments.append(f"photo{owner_id}_{photo_id}")
                    if len(photo_attachments) == attachments_before:
                        failed_photo_ids.append(file_id)
                except Exception as e:
                    logger.error(f"Error uploading photo {file_id}: {str(e)}")
                    failed_photo_ids.append(file_id)
                finally:
                    if temp_file and os.path.exists(temp_file):
                        os.unlink(temp_file)

            # Download and upload videos
            video_attachments = []
            for file_id in post.videos or []:
                temp_file = None
                try:
                    # Download video from Telegram
                    video_data = await self._download_telegram_file_with_retry(file_id)

                    if not video_data:
                        logger.error(f"Failed to download video {file_id}")
                        failed_video_ids.append(file_id)
                        continue

                    # Save video to temporary file
                    temp_file = f"/tmp/{file_id}.mp4"
                    with open(temp_file, "wb") as f:
                        f.write(video_data)

                    # Upload video to VK
                    upload_result = await self._upload_video_with_retry(
                        temp_file,
                        post.name,
                        formatted_text[:200] + "..." if len(formatted_text) > 200 else formatted_text,
                    )

                    # Format attachment string
                    owner_id = upload_result["owner_id"]
                    video_id = upload_result["video_id"]
                    video_attachments.append(f"video{owner_id}_{video_id}")
                except Exception as e:
                    logger.error(f"Error uploading video {file_id}: {str(e)}")
                    failed_video_ids.append(file_id)
                finally:
                    if temp_file and os.path.exists(temp_file):
                        os.unlink(temp_file)

            if _vk_upload_strict_mode() and (failed_photo_ids or failed_video_ids):
                raise RuntimeError(
                    f"VK upload strict mode: failed photos={failed_photo_ids}, videos={failed_video_ids}"
                )

            # --- VK Market: публикуем товар ДО wall.post, чтобы прикрепить карточку
            # товара к посту (даёт кнопку «Смотреть товары»). Только если включён
            # переключатель «Товары ВК». При выключенном — поведение как раньше.
            market_attachment = None
            try:
                from app.workers.vk.product_publisher import publish_product_to_vk
                from app.services.settings_service import get_settings_service

                if get_settings_service().is_vk_market_publish_allowed():
                    product_ok = await publish_product_to_vk(post_id)
                    if not product_ok:
                        msg = (
                            "VK Market publication failed; wall post was not published"
                        )
                        if _vk_wall_requires_market():
                            raise RuntimeError(msg)
                        logger.error(
                            "%s (wall-requires-market=off — publishing wall only)",
                            msg,
                        )
                    elif VK_WALL_ATTACH_MARKET:
                        # Свежая сессия: market INSERT мог испортить текущее соединение.
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        try:
                            db.close()
                        except Exception:
                            pass
                        db = SessionLocal()
                        product = fetch_product_row_by_post_id(db, post_id)
                        vk_product_id = product.get("vk_product_id") if product else None
                        if vk_product_id:
                            market_attachment = (
                                f"market-{self.group_id}_{vk_product_id}"
                            )
                            logger.info(
                                "Attaching market item to wall post %s: %s",
                                post_id,
                                market_attachment,
                            )
                        else:
                            logger.warning(
                                "VK market published but vk_product_id missing in DB for %s",
                                post_id,
                            )
            except Exception as e:
                logger.error(
                    f"Error publishing product before wall post {post_id}: {str(e)}"
                )
                if _vk_wall_requires_market():
                    raise
                logger.error(
                    "Continuing wall publish without market (wall-requires-market=off)"
                )

            # Combine all attachments
            media_attachments = photo_attachments + video_attachments
            attachments_list = list(media_attachments)
            if market_attachment:
                attachments_list.append(market_attachment)
            attachments = ",".join(attachments_list)

            # Post to VK wall. Если market-вложение отклонено API — повторяем без него,
            # чтобы не потерять публикацию в ленте.
            try:
                post_result = await asyncio.to_thread(
                    self._wall_post_sync, formatted_text, attachments
                )
            except Exception as e:
                if market_attachment:
                    logger.error(
                        f"wall.post failed with market attachment for post {post_id} "
                        f"({str(e)}); retrying without market attachment"
                    )
                    attachments = ",".join(media_attachments)
                    post_result = await asyncio.to_thread(
                        self._wall_post_sync, formatted_text, attachments
                    )
                    market_attachment = None
                else:
                    raise

            vk_post_id = post_result.get("post_id")
            vk_post_id_str = None
            vk_post_link = None
            if vk_post_id:
                owner_id = -self.group_id
                vk_post_id_str = f"{owner_id}_{vk_post_id}"
                from app.utils.vk_urls import wall_post_url

                vk_post_link = wall_post_url(owner_id, int(vk_post_id))

            # После market INSERT соединение могло умереть — сохраняем wall в свежей сессии.
            try:
                db.rollback()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass
            db = SessionLocal()
            mark_post_published_vk(
                db,
                post_id,
                vk_post_id=vk_post_id_str,
                vk_post_link=vk_post_link,
            )

            log_message = (
                f"Published to VK (photos {len(photo_attachments)}/{total_photos}, "
                f"videos {len(video_attachments)}/{total_videos}, "
                f"strict={_vk_upload_strict_mode()})"
            )
            if failed_photo_ids:
                log_message += f"; failed_photo_ids={','.join(failed_photo_ids)}"
            if failed_video_ids:
                log_message += f"; failed_video_ids={','.join(failed_video_ids)}"

            insert_publication_log(db, post_id, "vk", "success", log_message)

            db.commit()

            logger.info(f"Post {post_id} published to VK successfully")

            # Примечание: публикация товара в VK Market теперь выполняется ДО wall.post
            # (см. блок выше), чтобы прикрепить карточку товара к посту в ленте.

            # Автосторис ВК — независимо от Товаров ВК; ошибки не откатывают стену.
            try:
                from app.workers.vk.story_publisher import maybe_auto_publish_vk_story

                await maybe_auto_publish_vk_story(post_id)
            except Exception as story_err:
                logger.error(
                    "Auto VK story after wall.post failed for %s: %s",
                    post_id,
                    story_err,
                )

            return True
        except Exception as e:
            logger.error(f"Error publishing post {post_id} to VK: {str(e)}")
            try:
                db.rollback()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass
            try:
                db = SessionLocal()
                insert_publication_log(db, post_id, "vk", "error", str(e))
                db.commit()
            except Exception:
                logger.exception("Failed to write VK error publication log for %s", post_id)

            return False
        finally:
            db.close()

async def publish_post_to_vk(post_id, signature_enabled: bool = True):
    """Publish a post to VK."""
    publisher = VKPublisher()
    return await publisher.publish_post(post_id, signature_enabled=signature_enabled)
