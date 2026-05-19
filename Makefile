.PHONY: install run scan test clean \
        historical-download historical-list \
        modal-auth modal-download modal-backtest

install:
	pip install -r requirements.txt

run:
	streamlit run src/dashboard/app.py

scan:
	python -m src.scanner.scanner

test:
	pytest -q

clean:
	rm -rf data/cache/*.parquet logs/*.log
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# --- Historical store (local) -----------------------------------------------
historical-download:                      ## Download the full sector universe locally
	python -m src.data.historical download --all

historical-list:                          ## Summarize stored symbols
	python -m src.data.historical list

# --- Modal compute layer ----------------------------------------------------
modal-auth:                               ## One-time Modal authentication
	modal token new

modal-download:                           ## Refresh the stock_data volume on Modal (SYMBOLS=NVDA,PLTR)
	modal run src.modal_app.app::download --symbols "$(SYMBOLS)"

modal-backtest:                           ## Run a backtest on Modal (SYMBOLS=..., START=YYYY-MM-DD, END=YYYY-MM-DD)
	modal run src.modal_app.app::backtest --symbols "$(SYMBOLS)" --start $(START) --end $(END)
