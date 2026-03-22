FROM python:3.11-slim

RUN apt update && apt upgrade -y && apt install -y curl

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ade_bench.py test_generator.py server.py ./

EXPOSE 8080

CMD ["python", "server.py"]
