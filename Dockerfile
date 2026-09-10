FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sends traffic to whatever port is in the PORT env var
# (it sets this automatically — we just need to listen on it).
ENV PORT=8080
ENV RUN_MODE=cloud_run

CMD ["python", "src/bot.py"]
