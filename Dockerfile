# Eén image met de API én de statische frontend. LibreOffice zit erbij omdat
# PowerPoint- en Word-uploads daarmee naar PDF worden omgezet; zonder dat pakket
# werkt de app wel, maar vallen .pptx/.docx terug op alleen-tekst.
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

# /data is de plek waar uploads, afbeeldingen en accounts komen te staan. Koppel
# hier een persistent volume aan: zonder dat is alles weg bij elke nieuwe deploy.
VOLUME ["/data"]

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend:app --host 0.0.0.0 --port ${PORT:-8000}"]
