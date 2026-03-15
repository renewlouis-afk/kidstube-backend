FROM python:3.12-bookworm
RUN apt-get update && apt-get install -y ffmpeg ca-certificates && update-ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/ -r requirements.txt
RUN pip install --upgrade pymongo[srv] motor certifi
COPY server.py .
RUN mkdir -p /tmp/videos /tmp/audio /tmp/images
EXPOSE 10000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "10000"]
