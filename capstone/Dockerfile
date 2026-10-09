FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jre-headless procps make postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 \
    PYSPARK_PYTHON=python3 \
    PYSPARK_DRIVER_PYTHON=python3 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Postgres JDBC driver, baked in so Spark needs no network at runtime.
ENV POSTGRES_JDBC=/opt/jars/postgresql.jar
RUN mkdir -p /opt/jars && curl -sSL -o $POSTGRES_JDBC https://jdbc.postgresql.org/download/postgresql-42.7.4.jar

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["bash"]
