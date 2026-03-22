from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="nre_agent", version="0.1.0")

@app.get("/")
def root():
    return {"service": "nre_agent", "status": "running"}

@app.get("/health/live")
def health_live():
    return JSONResponse(content={"status": "alive"})

@app.get("/health/ready")
def health_ready():
    return JSONResponse(content={"status": "ready"})

@app.get("/version")
def version():
    return {"service": "nre_agent", "version": "0.1.0"}
