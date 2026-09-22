FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY tinkoff_api.py .
COPY recommender.py .
COPY ai_advisor.py .

CMD ["python", "bot.py"]