.PHONY: test api web

test:
	cd backend && ../.venv/Scripts/python.exe -m pytest -q

api:
	cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

web:
	cd frontend && npm run dev
