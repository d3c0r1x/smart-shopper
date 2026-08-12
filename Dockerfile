FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONIOENCODING=utf-8
ENV SHOPPER_DEMO_MODE=1

CMD ["python", "bot.py"]
