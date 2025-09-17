# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Install system dependencies
# - libtorrent-rasterbar is for the torrent engine
# - ffmpeg is for video processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    g++ \
    libtorrent-rasterbar-dev \
    python3-libtorrent \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Link the system-installed libtorrent to the local site-packages directory
RUN ln -s /usr/lib/python3/dist-packages/libtorrent.so /usr/local/lib/python3.9/site-packages/libtorrent.so

# Copy the requirements file into the container
COPY requirements.txt ./

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code
COPY . .

# Make port 6991 available to the world outside this container
EXPOSE 6991

# Run app.py when the container launches
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6991"]
