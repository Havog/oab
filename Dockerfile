FROM python:3.12-slim
 
# Debian has no packaged `oabextract`, so build a tiny one against libmspack.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libmspack-dev \
    && rm -rf /var/lib/apt/lists/*
 
WORKDIR /build
COPY oabextract.c .
RUN gcc -O2 -o /usr/local/bin/oabextract oabextract.c -lmspack \
    && test -x /usr/local/bin/oabextract
 
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY oab_bot.py .
 
CMD ["python", "-u", "oab_bot.py"]
