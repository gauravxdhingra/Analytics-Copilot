# The UI: built once, served by unprivileged nginx, which also forwards /auth and /api to the BFF
# so the browser only ever talks to one origin. Build context is the repository root.
# [VERIFY] pin the base images by digest at each release.
FROM node:24-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
# Explicit and checkable: nginx-unprivileged runs as uid 101, never as root.
USER 101
EXPOSE 8080
