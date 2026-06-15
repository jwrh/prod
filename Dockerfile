FROM python:3.12-slim@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3

WORKDIR /prod_next

COPY requirements.lock.txt ./

RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt

COPY adapters ./adapters
COPY domain ./domain
COPY observability ./observability
COPY runtime ./runtime
COPY strategies ./strategies
COPY cli.py main.py ./

RUN useradd --create-home --uid 1000 trader \
 && mkdir -p /prod_next/logs \
 && chown -R trader:trader /prod_next

USER trader

CMD ["python", "-m", "cli", "run", "--config", "/prod_next/config.yaml"]
