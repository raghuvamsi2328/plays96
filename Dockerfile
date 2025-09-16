# Use Node.js image
FROM node:18-alpine

# Install FFmpeg and basic build tools
RUN apk add --no-cache \
    ffmpeg \
    python3 \
    make \
    g++

# Optimize network settings for better torrent performance
RUN echo 'net.core.rmem_max = 134217728' >> /etc/sysctl.conf && \
    echo 'net.core.wmem_max = 134217728' >> /etc/sysctl.conf && \
    echo 'net.ipv4.tcp_rmem = 4096 87380 134217728' >> /etc/sysctl.conf && \
    echo 'net.ipv4.tcp_wmem = 4096 65536 134217728' >> /etc/sysctl.conf && \
    echo 'net.core.netdev_max_backlog = 5000' >> /etc/sysctl.conf

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

# Use index.js instead of server.js for the optimized version
CMD ["node", "index.js"]
