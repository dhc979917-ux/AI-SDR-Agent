FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sdr/ sdr/
COPY static/ static/
COPY prompts/ prompts/
COPY data/ data/
COPY scripts/init_admin.py scripts/init_admin.py
RUN useradd -m appuser && mkdir -p /app/var && chown -R appuser:appuser /app
USER appuser
EXPOSE 8765
CMD ["python", "-m", "sdr", "--host", "0.0.0.0", "--port", "8765"]
