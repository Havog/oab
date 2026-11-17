FROM python:3.12-slim

# oabextract (needed to decompress LZX-compressed .oab files)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmspack-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY oab_bot.py .

CMD ["python", "-u", "oab_bot.py"]
