# Use Node.js image with FFmpeg pre-installed
FROM linuxserver/ffmpeg:latest as ffmpeg
FROM node:18-alpine

# Copy FFmpeg from the ffmpeg image
COPY --from=ffmpeg /usr/local /usr/local
COPY --from=ffmpeg /usr/lib/lib* /usr/lib/

# Install additional dependencies
RUN apk add --no-cache \
    python3 \
    make \
    g++ \
    libtorrent-dev \
    boost-dev

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install Node.js dependencies
RUN npm install

# Copy application code
COPY . .

# Create downloads directory
RUN mkdir -p /app/downloads

EXPOSE 3000

CMD ["node", "index.js"]
