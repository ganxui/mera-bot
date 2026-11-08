FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONUNBUFFERED=1 MERA_DB=/app/data/mera.sqlite3
RUN mkdir -p /app/data && useradd --uid 10001 --create-home mera && chown -R mera:mera /app
USER mera
CMD ["python", "bot.py"]
