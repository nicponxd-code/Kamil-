\
    @echo off
    setlocal
    if not exist .venv (
        py -m venv .venv
    )
    call .\.venv\Scripts\activate
    pip install -r requirements.txt
    start cmd /k "call .\.venv\Scripts\activate && streamlit run app/ui/app_streamlit.py --server.port %STREAMLIT_PORT%"
    python -m app.main
