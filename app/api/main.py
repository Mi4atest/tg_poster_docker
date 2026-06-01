from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import posts, telegram, stories, scheduler, products, avito_oauth, avito_feed, vk_oauth
from app.db.database import engine, Base
from app.db.migrate import ensure_database_schema

# Create database tables
Base.metadata.create_all(bind=engine)

# Проверяем и применяем необходимые изменения схемы
ensure_database_schema()

# Create FastAPI app
app = FastAPI(
    title="Social Media Poster API",
    description="API for managing social media posts",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
app.include_router(telegram.router, prefix="/api/telegram", tags=["telegram"])
app.include_router(stories.router, prefix="/api/stories", tags=["stories"])
app.include_router(scheduler.router, prefix="/api/scheduler", tags=["scheduler"])
app.include_router(products.router, prefix="/api/products", tags=["products"])
app.include_router(avito_oauth.router, tags=["avito"])
app.include_router(vk_oauth.router, tags=["vk"])
app.include_router(avito_feed.router, tags=["avito"])

@app.get("/")
def read_root():
    return {"message": "Welcome to Social Media Poster API"}

# Run with: uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
