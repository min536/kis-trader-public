FROM python:3.13-slim

WORKDIR /workspace

RUN pip install --no-cache-dir "pandas>=2.2,<3.0" "numpy>=2.0,<3.0" "PyYAML>=6.0,<7.0"

ENV PYTHONPATH=/workspace
ENV KIS_TRADER_ROOT=/workspace
ENV PORT=4173
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 4173

CMD ["python", "workspace/claude-design/kis-trader-v2/server.py"]
