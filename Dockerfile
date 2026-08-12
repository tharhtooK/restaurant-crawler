# The tag's Playwright version must equal the pin in requirements.txt, or crawl4ai
# installs a Playwright whose browsers are not in this image.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# One worker on purpose: job state is an in-process dict, so a second worker would
# answer polls for jobs it cannot see.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]