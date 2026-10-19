# Copy this file to the ModelScope studio as Dockerfile.
# Update SOURCE_REVISION to a published GitHub commit before rebuilding.
FROM python:3.12-slim

ARG SOURCE_REVISION=9985ff6b923eb3e247a66db9ddeedf6dbf6e7b85
LABEL org.opencontainers.image.source="https://github.com/TroyeXie/huawei-pc-marketing-agent" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${SOURCE_REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860 ZHIHUA_RUNTIME_DIR=/tmp/zhi-hua-runtime
WORKDIR /app

ADD https://codeload.github.com/TroyeXie/huawei-pc-marketing-agent/zip/${SOURCE_REVISION} /tmp/zhihua-source.zip
RUN python -c "import pathlib, shutil, zipfile; target=pathlib.Path('/tmp/zhihua-source'); zipfile.ZipFile('/tmp/zhihua-source.zip').extractall(target); shutil.copytree(next(target.iterdir()), '/app', dirs_exist_ok=True); shutil.rmtree(target); pathlib.Path('/tmp/zhihua-source.zip').unlink()" \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 1000 --create-home appuser

USER appuser
EXPOSE 7860
CMD ["python", "app.py"]
