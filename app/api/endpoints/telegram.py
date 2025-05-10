from fastapi import APIRouter, HTTPException, Response
from aiogram import Bot
import aiohttp
import io

from app.config.settings import TELEGRAM_BOT_TOKEN

router = APIRouter()

@router.get("/file/{file_id}")
async def get_telegram_file(file_id: str):
    """Get a file from Telegram by file_id."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    try:
        # Get file info
        try:
            file_info = await bot.get_file(file_id)
            file_path = file_info.file_path
        except Exception as e:
            # Log the error
            print(f"Error getting file info from Telegram: {str(e)}")

            # Try to handle common file types based on file_id format
            # This is a fallback for when Telegram API doesn't return file info
            if "AgAC" in file_id:  # Photo file_id usually starts with AgAC
                # For photos, we'll create a dummy file path
                file_path = "photos/photo.jpg"
            elif "BAAC" in file_id:  # Video file_id usually starts with BAAC
                # For videos, we'll create a dummy file path
                file_path = "videos/video.mp4"
            else:
                # If we can't determine the file type, raise an exception
                raise HTTPException(status_code=400, detail=f"Unsupported file type or invalid file_id: {file_id}")

        # Get file URL
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
                        content = await response.read()

                        # Determine content type
                        content_type = "application/octet-stream"
                        if file_path.endswith(".jpg") or file_path.endswith(".jpeg"):
                            content_type = "image/jpeg"
                        elif file_path.endswith(".png"):
                            content_type = "image/png"
                        elif file_path.endswith(".mp4"):
                            content_type = "video/mp4"
                        elif file_path.endswith(".mov"):
                            content_type = "video/quicktime"

                        return Response(content=content, media_type=content_type)
                    else:
                        # Log the error
                        print(f"Failed to download file from Telegram: {response.status} - {await response.text()}")
                        raise HTTPException(status_code=response.status, detail="Failed to download file from Telegram")
            except Exception as e:
                # Log the error
                print(f"Error downloading file from Telegram: {str(e)}")

                # Try alternative method using curl
                try:
                    import tempfile
                    import os
                    import subprocess

                    # Create a temporary file
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as temp_file:
                        temp_path = temp_file.name

                    # Use curl to download the file (curl handles SSL issues better)
                    curl_cmd = [
                        "curl",
                        "-s",
                        "-k",  # Skip SSL verification
                        file_url,
                        "-o", temp_path
                    ]

                    process = subprocess.run(curl_cmd, capture_output=True)

                    if process.returncode == 0:
                        # Read the file
                        with open(temp_path, "rb") as f:
                            content = f.read()

                        # Clean up
                        os.unlink(temp_path)

                        # Determine content type
                        content_type = "application/octet-stream"
                        if file_path.endswith(".jpg") or file_path.endswith(".jpeg"):
                            content_type = "image/jpeg"
                        elif file_path.endswith(".png"):
                            content_type = "image/png"
                        elif file_path.endswith(".mp4"):
                            content_type = "video/mp4"
                        elif file_path.endswith(".mov"):
                            content_type = "video/quicktime"

                        return Response(content=content, media_type=content_type)
                    else:
                        # Log the error
                        print(f"Curl failed with return code {process.returncode}: {process.stderr.decode()}")
                        raise HTTPException(status_code=500, detail=f"Curl failed to download file: {process.stderr.decode()}")
                except Exception as curl_e:
                    # Log the error
                    print(f"Error using curl fallback: {str(curl_e)}")
                    raise HTTPException(status_code=500, detail=f"Error downloading file: {str(e)} (Curl fallback failed: {str(curl_e)})")
    except HTTPException as he:
        # Re-raise HTTP exceptions
        raise he
    except Exception as e:
        # Log the error
        print(f"Unexpected error getting file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting file: {str(e)}")
    finally:
        await bot.session.close()
