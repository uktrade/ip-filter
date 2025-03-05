FROM python:latest

WORKDIR /app
EXPOSE 8080

COPY . /app

RUN pip install -r requirements.txt
#RUN /bin/bash pip install --root-user-action django 
#RUN pip install --root-user-action gunicorn 

CMD ["gunicorn",  "-b 0.0.0.0:8080", "config.wsgi:application"]
