FROM python:3.11-slim

WORKDIR /app

# Copy backend code and requirements
COPY backend/ /app/backend/

RUN pip install --no-cache-dir -r /app/backend/requirements.txt

EXPOSE 8080

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]


