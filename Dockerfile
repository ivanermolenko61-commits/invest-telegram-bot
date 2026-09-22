FROM python:3.11-slim

WORKDIR /app

# Устанавливаем корневые сертификаты
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --trusted-host opensource.tbank.ru — обходит проверку SSL для корпоративного сертификата Т-Банка
RUN pip install --no-cache-dir \
    --trusted-host opensource.tbank.ru \
    -r requirements.txt

COPY bot.py .
COPY tinkoff_api.py .
COPY recommender.py .
COPY ai_advisor.py .

CMD ["python", "bot.py"]