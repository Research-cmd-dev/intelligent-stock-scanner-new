.PHONY: install run scan test clean

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
