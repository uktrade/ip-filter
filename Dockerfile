FROM python:3.11

RUN apt-get update && apt-get install -qq build-essential \
                                          libpq-dev \
                                          python3-dev \
                                          libffi-dev \
                                          libssl-dev \
                                          git \
                                          postgresql-client

WORKDIR /app

#COPY pyproject.toml /app
#RUN pip install poetry && poetry add honcho && poetry install


COPY . /app

ENV PORT 8000

COPY requirements.txt /app
RUN pip install -r requirements.txt
RUN chmod +x run.sh


CMD "./run.sh"
