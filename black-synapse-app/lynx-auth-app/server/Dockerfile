# ── Stage 1: dependencies ─────────────────────────────────────────────────────
FROM node:20-slim AS deps

WORKDIR /app

# better-sqlite3 requires native build tools
RUN apt-get update && apt-get install -y python3 make g++ && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --omit=dev

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM node:20-slim

WORKDIR /app

# Copy only production node_modules from deps stage
COPY --from=deps /app/node_modules ./node_modules

# Copy app source
COPY . .

# Create the data directory for SQLite
RUN mkdir -p data

EXPOSE 4000

CMD ["node", "index.js"]
