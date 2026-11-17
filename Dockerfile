FROM python:3.12-slim

RUN apt-get update \
    && (apt-get install -y --no-install-recommends mspack-tools \
        || apt-get install -y --no-install-recommends libmspack-tools) \
    && rm -rf /var/lib/apt/lists/* \
    && command -v oabextract

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY oab_bot.py .

CMD ["python", "-u", "oab_bot.py"]
