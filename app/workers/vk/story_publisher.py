import vk_api
import logging
import aiohttp
import asyncio
import requests
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import os
from PIL import Image, ImageDraw, ImageFont
import io
import json

from app.config.settings import VK_ACCESS_TOKEN, VK_GROUP_ID, API_HOST, API_PORT
from app.db.database import SessionLocal
from app.api.models.story import Story, StoryPublicationLog

logger = logging.getLogger(__name__)

class VKStoryPublisher:
    """Class for publishing stories to VK."""

    def __init__(self):
        """Initialize VK API session."""
        self.vk_session = vk_api.VkApi(token=VK_ACCESS_TOKEN, api_version="5.131")
        self.vk = self.vk_session.get_api()
        self.upload = vk_api.VkUpload(self.vk_session)

    async def download_telegram_file(self, file_id):
        """Download file from Telegram."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://{API_HOST}:{API_PORT}/api/telegram/file/{file_id}"
                logger.info(f"Downloading file from: {url}")

                async with session.get(url) as response:
                    if response.status == 200:
                        logger.info(f"Successfully downloaded file {file_id}")
                        return await response.read()
                    else:
                        logger.error(f"Failed to download file {file_id}: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error downloading file {file_id}: {str(e)}")
            return None

    def create_story_image(self, image_data, model_name, price):
        """Create a story image with model name and price overlay."""
        try:
            # Open the image
            image = Image.open(io.BytesIO(image_data))
            logger.info(f"Original image size: {image.size}")

            # Resize image to story format (9:16)
            width, height = image.size
            target_ratio = 9 / 16
            current_ratio = width / height

            if current_ratio > target_ratio:
                # Image is too wide, crop width
                new_width = int(height * target_ratio)
                left = (width - new_width) // 2
                image = image.crop((left, 0, left + new_width, height))
            elif current_ratio < target_ratio:
                # Image is too tall, crop height
                new_height = int(width / target_ratio)
                top = (height - new_height) // 2
                image = image.crop((0, top, width, top + new_height))

            # Resize to standard story size
            image = image.resize((1080, 1920))
            logger.info(f"Resized image to: {image.size}")

            # Create a drawing context
            draw = ImageDraw.Draw(image)

            # Try to load a font, fall back to default if not available
            font_large = None
            try:
                # Пробуем загрузить Arial
                font_large = ImageFont.truetype("arial.ttf", 80)
                logger.info("Using Arial font")
            except IOError:
                try:
                    # Пробуем загрузить системный шрифт Arial
                    font_large = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 80)
                    logger.info("Using system Arial font")
                except IOError:
                    try:
                        # Пробуем загрузить DejaVuSans
                        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 80)
                        logger.info("Using DejaVuSans font")
                    except IOError:
                        # Если не удалось, используем стандартный
                        font_large = ImageFont.load_default()
                        logger.info("Using default font")

            # Формируем текст в одну строку
            text = ""
            if model_name and price:
                text = f"{model_name} - {price}"
            elif model_name:
                text = model_name
            elif price:
                text = f"Цена: {price}"

            if text:
                logger.info(f"Adding text overlay: {text}")
                # Рисуем текст с обводкой для лучшей видимости на любом фоне
                # Сначала рисуем черную обводку
                for offset_x, offset_y in [(-2, -2), (-2, 2), (2, -2), (2, 2)]:
                    draw.text((540 + offset_x, 1800 + offset_y), text, font=font_large, fill=(0, 0, 0), anchor="ms")

                # Затем рисуем белый текст поверх
                draw.text((540, 1800), text, font=font_large, fill=(255, 255, 255), anchor="ms")

            # Save the image to a buffer
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=95)
            buffer.seek(0)

            return buffer.getvalue()
        except Exception as e:
            logger.error(f"Error creating story image: {str(e)}")
            return None

    async def publish_story(self, story_id):
        """Publish a story to VK."""
        db = SessionLocal()
        try:
            # Get story from database
            story = db.query(Story).filter(Story.id == story_id).first()

            if not story:
                logger.error(f"Story {story_id} not found")
                return False

            # Check if already published
            if story.is_published:
                logger.info(f"Story {story_id} already published to VK")
                return True

            # Download media file from Telegram
            if not story.media_file_id:
                logger.error(f"Story {story_id} has no media file")
                return False

            logger.info(f"Downloading media file for story {story_id}")
            media_data = await self.download_telegram_file(story.media_file_id)
            if not media_data:
                logger.error(f"Failed to download media file for story {story_id}")
                return False

            # Create story image with overlay
            logger.info(f"Creating story image for story {story_id}")
            story_image_data = self.create_story_image(media_data, story.model_name, story.price)
            if not story_image_data:
                logger.error(f"Failed to create story image for story {story_id}")
                return False

            # Save story image to temporary file
            temp_file = f"/tmp/vk_story_{story_id}.jpg"
            with open(temp_file, "wb") as f:
                f.write(story_image_data)
            logger.info(f"Saved story image to {temp_file}")

            # Для публикации сторис в группе ВКонтакте
            try:
                # Получаем адрес сервера для загрузки истории
                logger.info(f"Getting upload server for VK story, group_id={abs(int(VK_GROUP_ID))}")
                upload_server = self.vk.stories.getPhotoUploadServer(
                    add_to_news=1,  # Добавить в новости
                    group_id=abs(int(VK_GROUP_ID)),  # ID группы (положительное число)
                    user_ids=[],  # Пустой список пользователей
                )

                logger.info(f"VK upload server response: {upload_server}")

                if not upload_server or 'upload_url' not in upload_server:
                    logger.error(f"Failed to get upload server for VK story: {upload_server}")
                    raise Exception("Failed to get upload server for VK story")

                # Загружаем фото на сервер
                logger.info(f"Uploading story to VK server: {upload_server['upload_url']}")
                with open(temp_file, 'rb') as file:
                    response = requests.post(upload_server['upload_url'], files={'file': file})

                if response.status_code != 200:
                    logger.error(f"Failed to upload story to VK: {response.status_code} {response.text}")
                    raise Exception(f"Failed to upload story to VK: {response.status_code}")

                upload_data = response.json()
                logger.info(f"VK upload response: {upload_data}")

                # Проверяем наличие upload_result в ответе
                # В новой версии API VK upload_result находится внутри поля response
                upload_result = None
                if 'upload_result' in upload_data:
                    upload_result = upload_data['upload_result']
                elif 'response' in upload_data and 'upload_result' in upload_data['response']:
                    upload_result = upload_data['response']['upload_result']
                
                if not upload_result:
                    logger.error(f"Invalid upload response from VK: {upload_data}")
                    raise Exception("Invalid upload response from VK")

                # Сохраняем историю
                logger.info(f"Saving story to VK with upload_result: {upload_result[:30]}...")
                save_result = self.vk.stories.save(
                    upload_results=upload_result,
                    group_id=abs(int(VK_GROUP_ID))
                )

                logger.info(f"VK save result: {save_result}")

                # Проверяем результат сохранения
                if not save_result or not isinstance(save_result, list) or len(save_result) == 0:
                    logger.error(f"Failed to save story to VK: {save_result}")
                    raise Exception("Failed to save story to VK")

                # Получаем ID истории и ссылку
                story_data = save_result[0]
                owner_id = story_data.get('owner_id', VK_GROUP_ID)
                vk_story_id = story_data.get('id', 'unknown')
                story_link = f"https://vk.com/stories{owner_id}_{vk_story_id}"
                logger.info(f"Story published to VK: {story_link}")

                # Проверяем, что история действительно опубликована
                try:
                    # Получаем список историй группы
                    logger.info(f"Verifying story publication for group {VK_GROUP_ID}")
                    stories = self.vk.stories.get(owner_id=VK_GROUP_ID)
                    logger.info(f"VK stories response: {stories}")

                    if not stories or 'items' not in stories or len(stories['items']) == 0:
                        logger.warning(f"Story may not be published to VK: no stories found for group {VK_GROUP_ID}")
                        # Но продолжаем, так как сохранение истории прошло успешно
                except Exception as e:
                    logger.warning(f"Could not verify story publication: {str(e)}")
                    # Продолжаем, так как основная операция сохранения прошла успешно
            except Exception as e:
                logger.error(f"Error publishing story to VK: {str(e)}")
                raise Exception(f"Error publishing story to VK: {str(e)}")

            # Update story status in database
            story.is_published = True
            story.published_at = datetime.now(timezone.utc)
            story.post_link = story_link

            # Add publication log
            log = StoryPublicationLog(
                story_id=story.id,
                status="success",
                message="Published to VK"
            )
            db.add(log)

            db.commit()

            # Clean up temporary file
            if os.path.exists(temp_file):
                os.remove(temp_file)

            logger.info(f"Story {story_id} published to VK successfully")
            return True
        except Exception as e:
            logger.error(f"Error publishing story {story_id} to VK: {str(e)}")

            # Add error log
            log = StoryPublicationLog(
                story_id=story_id,
                status="error",
                message=str(e)
            )
            db.add(log)
            db.commit()

            return False
        finally:
            db.close()

    async def test_direct_vk_story_upload(self, image_path):
        """Test function to check direct VK story publishing."""
        logger.info(f"Testing direct VK story upload with image: {image_path}")
        
        # Проверяем, что файл существует
        if not os.path.exists(image_path):
            logger.error(f"Image file not found: {image_path}")
            return False
        
        # Получаем адрес сервера для загрузки истории
        logger.info(f"Getting upload server for VK story, group_id={abs(int(VK_GROUP_ID))}")
        upload_server = self.vk.stories.getPhotoUploadServer(
            add_to_news=1,
            group_id=abs(int(VK_GROUP_ID))
        )
        
        logger.info(f"VK upload server response: {upload_server}")
        
        if not upload_server or 'upload_url' not in upload_server:
            logger.error(f"Failed to get upload server for VK story: {upload_server}")
            return False
        
        # Загружаем фото на сервер
        logger.info(f"Uploading story to VK server: {upload_server['upload_url']}")
        with open(image_path, 'rb') as file:
            response = requests.post(upload_server['upload_url'], files={'file': file})
            
        if response.status_code != 200:
            logger.error(f"Failed to upload story to VK: {response.status_code} {response.text}")
            return False
        
        upload_data = response.json()
        logger.info(f"VK upload response: {upload_data}")
        
        # Проверяем наличие upload_result в ответе
        upload_result = None
        if 'upload_result' in upload_data:
            upload_result = upload_data['upload_result']
        elif 'response' in upload_data and 'upload_result' in upload_data['response']:
            upload_result = upload_data['response']['upload_result']
            
        if not upload_result:
            logger.error(f"Invalid upload response from VK: {upload_data}")
            return False
        
        # Сохраняем историю
        logger.info(f"Saving story to VK with upload_result: {upload_result[:30]}...")
        save_result = self.vk.stories.save(
            upload_results=upload_result,
            group_id=abs(int(VK_GROUP_ID))
        )
        
        logger.info(f"VK save result: {save_result}")
        
        if not save_result or not isinstance(save_result, list) or len(save_result) == 0:
            logger.error(f"Failed to save story to VK: {save_result}")
            return False
        
        # Получаем ID истории и ссылку
        story_data = save_result[0]
        owner_id = story_data.get('owner_id', VK_GROUP_ID)
        vk_story_id = story_data.get('id', 'unknown')
        story_link = f"https://vk.com/stories{owner_id}_{vk_story_id}"
        logger.info(f"Story published to VK: {story_link}")
        
        return True

async def publish_story_to_vk(story_id):
    """Publish a story to VK."""
    publisher = VKStoryPublisher()
    return await publisher.publish_story(story_id)

# Функция для тестирования публикации сторис
async def test_vk_story_publisher(story_id):
    """Test function to check VK story publishing."""
    logger.info(f"Testing VK story publisher with story ID: {story_id}")
    
    # Создаем экземпляр издателя
    publisher = VKStoryPublisher()
    
    # Получаем информацию о сторис из базы данных
    db = SessionLocal()
    try:
        story = db.query(Story).filter(Story.id == story_id).first()
        if not story:
            logger.error(f"Test failed: Story {story_id} not found")
            return False
            
        logger.info(f"Story details: ID={story.id}, Media ID={story.media_file_id}, Model={story.model_name}, Price={story.price}")
        
        # Проверяем токен VK
        if not VK_ACCESS_TOKEN:
            logger.error("Test failed: VK_ACCESS_TOKEN is not set")
            return False
            
        logger.info(f"VK_ACCESS_TOKEN is set: {VK_ACCESS_TOKEN[:5]}...{VK_ACCESS_TOKEN[-5:]}")
        
        # Проверяем ID группы VK
        if not VK_GROUP_ID:
            logger.error("Test failed: VK_GROUP_ID is not set")
            return False
            
        logger.info(f"VK_GROUP_ID is set: {VK_GROUP_ID}")
        
        # Проверяем соединение с VK API
        try:
            vk_session = vk_api.VkApi(token=VK_ACCESS_TOKEN, api_version="5.131")
            vk = vk_session.get_api()
            group_info = vk.groups.getById(group_id=abs(int(VK_GROUP_ID)))
            logger.info(f"Successfully connected to VK API. Group info: {group_info}")
        except Exception as e:
            logger.error(f"Test failed: Could not connect to VK API: {str(e)}")
            return False
        
        # Проверяем возможность скачивания файла
        media_data = await publisher.download_telegram_file(story.media_file_id)
        if not media_data:
            logger.error(f"Test failed: Could not download media file {story.media_file_id}")
            return False
            
        logger.info(f"Successfully downloaded media file. Size: {len(media_data)} bytes")
        
        # Проверяем создание изображения
        story_image = publisher.create_story_image(media_data, story.model_name, story.price)
        if not story_image:
            logger.error("Test failed: Could not create story image")
            return False
            
        logger.info(f"Successfully created story image. Size: {len(story_image)} bytes")
        
        # Проверяем получение сервера для загрузки
        try:
            upload_server = vk.stories.getPhotoUploadServer(
                add_to_news=1,
                group_id=abs(int(VK_GROUP_ID)),
                user_ids=[]
            )
            logger.info(f"Successfully got upload server: {upload_server}")
        except Exception as e:
            logger.error(f"Test failed: Could not get upload server: {str(e)}")
            return False
        
        logger.info("All preliminary tests passed. Ready to publish story.")
        
        # Публикуем сторис
        result = await publisher.publish_story(story_id)
        if result:
            logger.info("Test successful: Story published to VK")
            return True
        else:
            logger.error("Test failed: Could not publish story to VK")
            return False
    except Exception as e:
        logger.error(f"Test failed with exception: {str(e)}")
        return False
    finally:
        db.close()

# Если файл запущен напрямую, выполняем тест
if __name__ == "__main__":
    import sys
    
    # Настраиваем логирование
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    if len(sys.argv) < 2:
        print("Usage: python story_publisher.py <story_id> [test_image_path]")
        sys.exit(1)
    
    story_id = sys.argv[1]
    
    # Если указан путь к тестовому изображению, запускаем прямой тест
    if len(sys.argv) > 2:
        test_image_path = sys.argv[2]
        publisher = VKStoryPublisher()
        result = asyncio.run(publisher.test_direct_vk_story_upload(test_image_path))
        print(f"Direct test result: {result}")
    else:
        # Запускаем обычный тест
        result = asyncio.run(test_vk_story_publisher(story_id))
        print(f"Test result: {result}")
