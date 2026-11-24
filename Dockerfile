FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
