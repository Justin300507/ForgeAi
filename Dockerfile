# Backend-only image: the frontend is built and served separately on Vercel
# (frontend/vercel.json), so this no longer bundles a frontend-builder stage
# or copies frontend/dist in -- main.py's "serve React frontend" block
# already no-ops safely when that directory doesn't exist.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl lsof psmisc \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g wrangler \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global init.defaultBranch main
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# playwright is a pip dependency but its browser binaries are a SEPARATE
# download this never ran -- every browser-dependent verification stage
# (Playwright page load, workflow tests) crashed with "Executable doesn't
# exist" the moment dist/ actually built successfully, and the regression
# guard (correctly) reverted every fix that got that far, in an endless loop.
RUN playwright install --with-deps chromium
COPY backend/ ./
COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8080
CMD ["/start.sh"]
