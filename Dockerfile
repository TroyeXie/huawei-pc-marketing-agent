FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860 ZHIHUA_RUNTIME_DIR=/tmp/zhi-hua-runtime
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py engine.py ima_client.py ./
COPY static ./static
COPY prompts ./prompts
COPY examples ./examples
RUN useradd --uid 1000 --create-home appuser
USER appuser
EXPOSE 7860
CMD ["python", "app.py"]
