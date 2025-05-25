from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import posts, telegram, stories
from app.db.database import engine, Base

# Create database tables
Base.metadata.create_all(bind=engine)

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

@app.get("/")
def read_root():
    return {"message": "Welcome to Social Media Poster API"}

# Run with: uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
