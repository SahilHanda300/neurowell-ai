FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install Microsoft ODBC Driver 18 for SQL Server inside the image.
RUN apt-get update \
    ; apt-get install -y --no-install-recommends curl gnupg2 ca-certificates apt-transport-https unixodbc unixodbc-dev \
    ; curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    ; echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/microsoft-prod.list \
    ; apt-get update \
    ; ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    ; rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "gunicorn run:app --bind 0.0.0.0:${PORT:-10000} --workers 2 --timeout 60"]
