# Use Node.js image
FROM node:18-alpine

# Install FFmpeg and basic build tools
RUN apk add --no-cache \
    ffmpeg \
    python3 \
    make \
    g++

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install Node.js dependencies
RUN npm install

# Copy application code
COPY . .

# Create downloads directory
RUN mkdir -p /app/downloads

EXPOSE 6991

CMD ["node", "index.js"]
