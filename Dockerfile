# Eén image met de API én de statische frontend. LibreOffice zit erbij omdat
# PowerPoint- en Word-uploads daarmee naar PDF worden omgezet; zonder dat pakket
# werkt de app wel, maar valt .pptx terug op alleen-tekst; .ppt/.docx hebben
# de conversie nodig om uitgelezen te kunnen worden.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BACKEND_CACHE_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress libreoffice-writer fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# /data is de plek waar uploads, afbeeldingen en accounts komen te staan.
# Railway accepteert geen Docker VOLUME-instructie: koppel daar via de service-
# instellingen een persistent volume met mount path /data aan.

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=45s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health', timeout=5)" || exit 1
CMD ["sh", "-c", "uvicorn backend:app --host 0.0.0.0 --port ${PORT:-8000}"]
