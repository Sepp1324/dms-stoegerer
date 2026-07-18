# --- Build-Stufe ---
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Deterministische Installation aus dem Lockfile (reproduzierbare CI-Builds).
RUN npm ci
COPY . .
# Vitest-Suite (#9) als Teil des Builds: ein roter Test lässt den Image-Build –
# und damit den PR-Check (frontend-build) – fehlschlagen, bevor gemergt wird.
RUN npm run test
RUN npm run build

# --- Laufzeit: statische Auslieferung via nginx ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
