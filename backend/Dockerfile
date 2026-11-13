FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home appuser
COPY --chown=appuser:appuser . .
# Existing Windows checkouts can still contain CRLF before .gitattributes is applied.
RUN sed -i 's/\r$//' scripts/start.sh
RUN mkdir -p /var/lib/resolveai/uploads && chown appuser:appuser /var/lib/resolveai/uploads
USER appuser
EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
