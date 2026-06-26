FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Cloud Run injects PORT (8080). Default to 8000 for local runs.
ENV PORT=8000
EXPOSE 8000
# Shell form so ${PORT} expands; exec so uvicorn is PID 1 and gets shutdown signals.
CMD exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}
