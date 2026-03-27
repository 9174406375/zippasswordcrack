web: gunicorn -w 1 --threads 16 --timeout 600 --keep-alive 15 --worker-class gthread --max-requests 1000 --max-requests-jitter 100 -b 0.0.0.0:$PORT crackpro:app
