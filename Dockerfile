FROM python:3.11-slim

WORKDIR /app

# Устанавливаем корневые сертификаты
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Серверы Т-Банка (pip-индекс opensource.tbank.ru и API invest-public-api.tbank.ru)
# используют сертификаты УЦ Минцифры, которых нет в стандартных списках.
# Добавляем их в системное хранилище вместо отключения проверки SSL
# (раньше здесь был --trusted-host) и указываем pip и gRPC брать его.
COPY certs/*.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY tinkoff_api.py .
COPY recommender.py .
COPY ai_advisor.py .

CMD ["python", "bot.py"]
