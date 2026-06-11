FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# Rewrites are compiled into the routes manifest at build time, so the backend
# origin must be known here; "backend" is the compose service hostname.
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=$BACKEND_URL
RUN npm run build

FROM node:20-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/next.config.ts ./

EXPOSE 3000

# BACKEND_URL is read by next.config.ts at server start (compose sets it to the
# backend service; local `npm run start` falls back to localhost:8000).
CMD ["npm", "run", "start"]
