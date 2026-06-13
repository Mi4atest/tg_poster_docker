import vk_api
import logging
import aiohttp
import requests
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.config.settings import (
    API_HOST,
    API_PORT,
    VK_UPLOAD_STRICT_MODE,
    VK_WALL_ATTACH_MARKET,
)
from app.utils.vk_client import get_community_vk_session, resolved_vk_group_id_int
from app.db.database import SessionLocal
from app.api.models.post import Post, PublicationLog
from app.api.models.product import Product
from app.utils.text_formatter import format_for_vk

logger = logging.getLogger(__name__)

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

    async def publish_post(self, post_id, signature_enabled: bool = True):
        """Publish a post to VK."""
        db = SessionLocal()
        try:
            # Get post from database
            post = db.query(Post).filter(Post.id == post_id).first()

            if not post:
                logger.error(f"Post {post_id} not found")
                return False

            # Log if already published, but continue with republishing
            if post.is_published_vk:
                logger.info(f"Post {post_id} already published to VK, republishing")

            # Get post text and format it
            text = format_for_vk(post.text, signature_enabled=signature_enabled)

            # Download and upload photos
            photo_attachments = []
            failed_photo_ids: list[str] = []
            failed_video_ids: list[str] = []
            total_photos = len(post.photos or [])
            total_videos = len(post.videos or [])

            for file_id in post.photos or []:
                attachments_before = len(photo_attachments)
                try:
                    # Download photo from Telegram
                    photo_data = await self.download_telegram_file(file_id)

                    if not photo_data:
                        logger.error(f"Failed to download photo {file_id}")
                        failed_photo_ids.append(file_id)
                        continue

                    # Save photo to temporary file
                    temp_file = f"/tmp/{file_id}.jpg"
                    with open(temp_file, "wb") as f:
                        f.write(photo_data)

                    # Upload photo to VK wall
                    try:
                        # Try using photo_wall method
                        upload_result = self.upload.photo_wall(
                            temp_file,
                            group_id=self.group_id,
                        )
                    except Exception as e:
                        logger.error(f"Error using photo_wall: {str(e)}")
                        # Fallback to regular photo upload
                        try:
                            # Create an album if needed
                            albums = self.vk.photos.getAlbums(owner_id=-self.group_id)
                            album_id = None

                            # Look for a "Wall Photos" album
                            for album in albums.get("items", []):
                                if album.get("title") == "Wall Photos":
                                    album_id = album.get("id")
                                    break

                            # If no album found, create one
                            if not album_id:
                                album = self.vk.photos.createAlbum(
                                    title="Wall Photos",
                                    group_id=self.group_id,
                                    description="Photos for wall posts",
                                )
                                album_id = album.get("id")

                            # Upload to the album
                            upload_result = self.upload.photo(
                                temp_file,
                                album_id=album_id,
                                group_id=self.group_id,
                            )
                        except Exception as e2:
                            logger.error(f"Error with fallback photo upload: {str(e2)}")
                            # Last resort - try uploading to wall directly
                            upload_server = self.vk.photos.getWallUploadServer(group_id=self.group_id)

                            # Upload photo to server
                            with open(temp_file, 'rb') as f:
                                response = requests.post(upload_server['upload_url'], files={'photo': f}).json()

                            # Save photo to wall
                            save_result = self.vk.photos.saveWallPhoto(
                                group_id=self.group_id,
                                photo=response["photo"],
                                server=response["server"],
                                hash=response["hash"],
                            )

                            upload_result = save_result

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

            # Download and upload videos
            video_attachments = []
            for file_id in post.videos or []:
                try:
                    # Download video from Telegram
                    video_data = await self.download_telegram_file(file_id)

                    if not video_data:
                        logger.error(f"Failed to download video {file_id}")
                        failed_video_ids.append(file_id)
                        continue

                    # Save video to temporary file
                    temp_file = f"/tmp/{file_id}.mp4"
                    with open(temp_file, "wb") as f:
                        f.write(video_data)

                    # Upload video to VK
                    upload_result = self.upload.video(
                        video_file=temp_file,
                        name=post.name,
                        description=text[:200] + "..." if len(text) > 200 else text,
                        group_id=self.group_id,
                    )

                    # Format attachment string
                    owner_id = upload_result["owner_id"]
                    video_id = upload_result["video_id"]
                    video_attachments.append(f"video{owner_id}_{video_id}")
                except Exception as e:
                    logger.error(f"Error uploading video {file_id}: {str(e)}")
                    failed_video_ids.append(file_id)

            if VK_UPLOAD_STRICT_MODE and (failed_photo_ids or failed_video_ids):
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
                    if product_ok and VK_WALL_ATTACH_MARKET:
                        # Товар создан в отдельной сессии и уже закоммичен —
                        # читаем его свежим запросом, чтобы получить vk_product_id.
                        db.expire_all()
                        product = (
                            db.query(Product).filter(Product.post_id == post_id).first()
                        )
                        if product and product.vk_product_id:
                            market_attachment = (
                                f"market-{self.group_id}_{product.vk_product_id}"
                            )
                            logger.info(
                                f"Attaching market item to wall post {post_id}: {market_attachment}"
                            )
            except Exception as e:
                # Не блокируем публикацию поста, если товар не опубликовался
                logger.error(
                    f"Error publishing product before wall post {post_id}: {str(e)}"
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
                post_result = self.vk.wall.post(
                    owner_id=-self.group_id,
                    from_group=1,  # Post as group
                    message=text,
                    attachments=attachments
                )
            except Exception as e:
                if market_attachment:
                    logger.error(
                        f"wall.post failed with market attachment for post {post_id} "
                        f"({str(e)}); retrying without market attachment"
                    )
                    attachments = ",".join(media_attachments)
                    post_result = self.vk.wall.post(
                        owner_id=-self.group_id,
                        from_group=1,
                        message=text,
                        attachments=attachments
                    )
                    market_attachment = None
                else:
                    raise

            # Сохраняем ID поста и ссылку
            vk_post_id = post_result.get('post_id')
            if vk_post_id:
                owner_id = -self.group_id
                post.vk_post_id = f"{owner_id}_{vk_post_id}"
                post.vk_post_link = f"https://vk.com/wall{owner_id}_{vk_post_id}"

            # Update post status in database
            post.is_published_vk = True
            post.published_vk_at = datetime.now(timezone.utc)

            log_message = (
                f"Published to VK (photos {len(photo_attachments)}/{total_photos}, "
                f"videos {len(video_attachments)}/{total_videos}, strict={VK_UPLOAD_STRICT_MODE})"
            )
            if failed_photo_ids:
                log_message += f"; failed_photo_ids={','.join(failed_photo_ids)}"
            if failed_video_ids:
                log_message += f"; failed_video_ids={','.join(failed_video_ids)}"

            log = PublicationLog(
                post_id=post.id,
                platform="vk",
                status="success",
                message=log_message,
            )
            db.add(log)

            db.commit()

            logger.info(f"Post {post_id} published to VK successfully")

            # Примечание: публикация товара в VK Market теперь выполняется ДО wall.post
            # (см. блок выше), чтобы прикрепить карточку товара к посту в ленте.

            return True
        except Exception as e:
            logger.error(f"Error publishing post {post_id} to VK: {str(e)}")

            # Add error log
            log = PublicationLog(
                post_id=post_id,
                platform="vk",
                status="error",
                message=str(e)
            )
            db.add(log)
            db.commit()

            return False
        finally:
            db.close()

async def publish_post_to_vk(post_id, signature_enabled: bool = True):
    """Publish a post to VK."""
    publisher = VKPublisher()
    return await publisher.publish_post(post_id, signature_enabled=signature_enabled)
