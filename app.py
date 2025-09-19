import asyncio
import logging
import os

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
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    os.makedirs(HLS_PATH, exist_ok=True)
    logging.info(f"Download path: {DOWNLOAD_PATH}")
    logging.info(f"HLS path: {HLS_PATH}")

    # Start background tasks
    asyncio.create_task(alert_listener())
    asyncio.create_task(cleanup_inactive_streams())
    logging.info("Background tasks started.")

@app.on_event("shutdown")
def shutdown_event():
    """
    On shutdown, save resume data for active torrents.
    """
    logging.info("Shutting down. Saving resume data...")
    ses = get_session()
    ses.post_torrent_updates()
    # Note: In a real app, you'd want to handle this more gracefully,
    # maybe giving some time for alerts to be processed.
    logging.info("Resume data saved.")


# --- API Routers ---
app.include_router(torrents.router, prefix="/api", tags=["torrents"])
app.include_router(streaming.router, prefix="/api", tags=["streaming"])


# --- Static Files ---
# Serve the test HTML file at the root
@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(os.path.join("public", "test.html"))

app.mount("/public", StaticFiles(directory="public"), name="public")


# --- Main Entry Point ---
if __name__ == "__main__":
    logging.info(f"Starting server on http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
