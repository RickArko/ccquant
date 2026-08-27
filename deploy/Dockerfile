# syntax=docker/dockerfile:1.7
# Static Market Tracker image — nginx only. No Python, no research DB, no secrets.
FROM nginx:1.30.4-alpine

RUN apk add --no-cache curl \
    && rm -rf /usr/share/nginx/html/*

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/public/ /usr/share/nginx/html/

# Fail the image build if the operator forgot to stage HTML.
RUN test -s /usr/share/nginx/html/index.html

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8080/healthz || exit 1
