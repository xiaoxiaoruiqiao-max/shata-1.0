FROM python:3.12-slim
WORKDIR /app
COPY dashboard.py pilot.py shadow.py ./
COPY web/ ./web/
COPY data/history.csv data/closures.json data/shadow_red_seed.csv data/dual_history.csv ./data/
COPY data/momentum_history.csv data/dividend_trade_prices.csv ./data/
RUN useradd --system --uid 10001 dashboard && install -d -o dashboard -g dashboard /state
USER dashboard
ENV ETF_BIND=0.0.0.0 ETF_STATE_DIR=/state PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "dashboard.py", "serve"]
