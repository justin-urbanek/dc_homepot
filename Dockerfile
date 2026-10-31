FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

# Same uid/gid as jubr on the homelab, so data/quarantine.json on the host
# belongs to jubr. data/ is mounted from the host (see compose.yaml).
USER 1000:100
ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
