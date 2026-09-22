FROM python:3.11-slim

WORKDIR /app

# Устанавливаем корневые сертификаты (без них pip не может подключиться к opensource.tbank.ru)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY tinkoff_api.py .
COPY recommender.py .
COPY ai_advisor.py .

CMD ["python", "bot.py"]