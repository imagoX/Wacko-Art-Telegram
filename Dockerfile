FROM alpine:latest

RUN apk add --no-cache python3 py3-pip python3-dev build-base

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "bot.py"]