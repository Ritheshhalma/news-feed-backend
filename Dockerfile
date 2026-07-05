FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install Playwright browser binaries and Camoufox patched Firefox
# camoufox fetch downloads its own hardened Firefox build (~700MB, cached in image layer)
RUN playwright install firefox --with-deps && python -m camoufox fetch
# Patch Playwright Firefox driver: null-check pageError.location before accessing .url/.lineNumber/.columnNumber
# (Playwright 1.61 bug — crashes Node.js process when a page throws an uncaught JS error with no location)
RUN BUNDLE=/usr/local/lib/python3.12/site-packages/playwright/driver/package/lib/coreBundle.js && \
    sed -i \
      "s/url: pageError\.location\.url,/url: pageError.location?.url ?? '',/g; \
       s/line: pageError\.location\.lineNumber,/line: pageError.location?.lineNumber ?? 0,/g; \
       s/column: pageError\.location\.columnNumber/column: pageError.location?.columnNumber ?? 0/g" \
    "$BUNDLE" && \
    grep -qF 'pageError.location?.url' "$BUNDLE" || \
    { echo "Playwright null-location patch not applied — coreBundle.js structure changed; update Dockerfile sed strings"; exit 1; }
COPY . .
RUN DJANGO_SETTINGS_MODULE=config.settings.base \
    DATABASE_URL=sqlite:///tmp/build.db \
    python manage.py collectstatic --noinput
EXPOSE 8000
