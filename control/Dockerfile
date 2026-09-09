FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends postgresql-client ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY control/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY control/app ./app
COPY control/site ./site
COPY cli ./cli
COPY skill ./skill
COPY examples ./examples
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
