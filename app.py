import asyncio
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api import streaming, torrents
from src.background import alert_listener, cleanup_inactive_streams
from src.config import DOWNLOAD_PATH, HLS_PATH, PORT
from src.state import get_session

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- App Initialization ---
app = FastAPI(title="Torrent Streamer")

# --- CORS Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Event Handlers ---
@app.on_event("startup")
async def startup_event():
    """
    On startup, create necessary directories and start background tasks.
    """
    # Convert to Path objects for better path handling
    download_path = Path(DOWNLOAD_PATH)
    hls_path = Path(HLS_PATH)
    
    # Create directories with proper permissions
    try:
        download_path.mkdir(parents=True, exist_ok=True, mode=0o777)
        hls_path.mkdir(parents=True, exist_ok=True, mode=0o777)
        logger.info(f"Download path: {download_path.absolute()}")
        logger.info(f"HLS path: {hls_path.absolute()}")
        
        # Verify directories are writable
        test_file = download_path / ".write_test"
        test_file.touch()
        test_file.unlink()
        logger.info("Download directory is writable ✓")
        
        test_file = hls_path / ".write_test"
        test_file.touch()
        test_file.unlink()
        logger.info("HLS directory is writable ✓")
        
    except PermissionError as e:
        logger.error(f"Permission denied creating directories: {e}")
        logger.error("Please ensure the application has write permissions to the directories")
    except Exception as e:
        logger.error(f"Error creating directories: {e}")

    # Start background tasks
    try:
        asyncio.create_task(alert_listener())
        asyncio.create_task(cleanup_inactive_streams())
        logger.info("Background tasks started ✓")
    except Exception as e:
        logger.error(f"Error starting background tasks: {e}")

@app.on_event("shutdown")
def shutdown_event():
    """
    On shutdown, save resume data for active torrents.
    """
    logger.info("Shutting down. Saving resume data...")
    try:
        ses = get_session()
        ses.post_torrent_updates()
        # Give some time for alerts to be processed
        import time
        time.sleep(2)
        logger.info("Resume data saved ✓")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


# --- Health Check ---
@app.get("/health", include_in_schema=False)
async def health_check():
    """
    Health check endpoint for Docker and monitoring.
    """
    return {
        "status": "healthy",
        "download_path": str(Path(DOWNLOAD_PATH).absolute()),
        "hls_path": str(Path(HLS_PATH).absolute()),
        "download_exists": Path(DOWNLOAD_PATH).exists(),
        "hls_exists": Path(HLS_PATH).exists(),
    }


# --- API Routers ---
app.include_router(torrents.router, prefix="/api/torrents", tags=["torrents"])
app.include_router(streaming.router, prefix="/api/stream", tags=["streaming"])


# --- Static Files ---
# Serve the test HTML file at the root
@app.get("/", include_in_schema=False)
async def root():
    """
    Serve the main test page.
    """
    public_path = Path("public")
    html_file = public_path / "test.html"
    
    if not html_file.exists():
        logger.warning(f"test.html not found at {html_file.absolute()}")
        return {"message": "Torrent Streamer API", "docs": "/docs"}
    
    return FileResponse(html_file)

# Mount static files directory
public_dir = Path("public")
if public_dir.exists():
    app.mount("/public", StaticFiles(directory=str(public_dir)), name="public")
    logger.info(f"Serving static files from {public_dir.absolute()}")
else:
    logger.warning(f"Public directory not found at {public_dir.absolute()}")


# --- Main Entry Point ---
if __name__ == "__main__":
    logger.info(f"Starting Torrent Streamer on http://0.0.0.0:{PORT}")
    logger.info(f"API Documentation: http://0.0.0.0:{PORT}/docs")
    logger.info(f"Health Check: http://0.0.0.0:{PORT}/health")
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=PORT,
        log_level="info"
    )