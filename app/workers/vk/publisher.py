import vk_api
import logging
import aiohttp
import asyncio
import requests
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.config.settings import VK_ACCESS_TOKEN, VK_GROUP_ID, API_HOST, API_PORT
from app.db.database import SessionLocal
from app.api.models.post import Post, PublicationLog
from app.utils.text_formatter import format_for_vk

logger = logging.getLogger(__name__)

class VKPublisher:
    """Class for publishing posts to VK."""

    def __init__(self):
        """Initialize VK API session."""
        self.vk_session = vk_api.VkApi(token=VK_ACCESS_TOKEN)
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

    async def publish_post(self, post_id):
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
            text = format_for_vk(post.text)

            # Download and upload photos
            photo_attachments = []
            for file_id in post.photos:
                try:
                    # Download photo from Telegram
                    photo_data = await self.download_telegram_file(file_id)

                    if not photo_data:
                        logger.error(f"Failed to download photo {file_id}")
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
                            group_id=abs(int(VK_GROUP_ID))
                        )
                    except Exception as e:
                        logger.error(f"Error using photo_wall: {str(e)}")
                        # Fallback to regular photo upload
                        try:
                            # Create an album if needed
                            albums = self.vk.photos.getAlbums(owner_id=-abs(int(VK_GROUP_ID)))
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
                                    group_id=abs(int(VK_GROUP_ID)),
                                    description="Photos for wall posts"
                                )
                                album_id = album.get("id")

                            # Upload to the album
                            upload_result = self.upload.photo(
                                temp_file,
                                album_id=album_id,
                                group_id=abs(int(VK_GROUP_ID))
                            )
                        except Exception as e2:
                            logger.error(f"Error with fallback photo upload: {str(e2)}")
                            # Last resort - try uploading to wall directly
                            upload_server = self.vk.photos.getWallUploadServer(group_id=abs(int(VK_GROUP_ID)))

                            # Upload photo to server
                            with open(temp_file, 'rb') as f:
                                response = requests.post(upload_server['upload_url'], files={'photo': f}).json()

                            # Save photo to wall
                            save_result = self.vk.photos.saveWallPhoto(
                                group_id=abs(int(VK_GROUP_ID)),
                                photo=response['photo'],
                                server=response['server'],
                                hash=response['hash']
                            )

                            upload_result = save_result

                    # Format attachment string
                    for photo in upload_result:
                        owner_id = photo["owner_id"]
                        photo_id = photo["id"]
                        photo_attachments.append(f"photo{owner_id}_{photo_id}")
                except Exception as e:
                    logger.error(f"Error uploading photo {file_id}: {str(e)}")

            # Download and upload videos
            video_attachments = []
            for file_id in post.videos:
                try:
                    # Download video from Telegram
                    video_data = await self.download_telegram_file(file_id)

                    if not video_data:
                        logger.error(f"Failed to download video {file_id}")
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
                        group_id=abs(int(VK_GROUP_ID))
                    )

                    # Format attachment string
                    owner_id = upload_result["owner_id"]
                    video_id = upload_result["video_id"]
                    video_attachments.append(f"video{owner_id}_{video_id}")
                except Exception as e:
                    logger.error(f"Error uploading video {file_id}: {str(e)}")

            # Combine all attachments
            attachments = ",".join(photo_attachments + video_attachments)

            # Post to VK wall
            self.vk.wall.post(
                owner_id=-abs(int(VK_GROUP_ID)),  # Negative ID for group
                from_group=1,  # Post as group
                message=text,
                attachments=attachments
            )

            # Update post status in database
            post.is_published_vk = True
            post.published_vk_at = datetime.now(timezone.utc)

            # Add publication log
            log = PublicationLog(
                post_id=post.id,
                platform="vk",
                status="success",
                message="Published to VK"
            )
            db.add(log)

            db.commit()

            logger.info(f"Post {post_id} published to VK successfully")
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

async def publish_post_to_vk(post_id):
    """Publish a post to VK."""
    publisher = VKPublisher()
    return await publisher.publish_post(post_id)
