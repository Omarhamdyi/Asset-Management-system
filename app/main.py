from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.routers import ingest 


app = FastAPI(title="Buguard Asset Management System")
app.include_router(ingest.router)

@app.get("/")
def health_check():
    return {"status": "healthy"}


@app.get("/test-db")
def test_db(db: Session = Depends(get_db)):
    try:

        db.execute("SELECT 1")
        return {"status": "Database connection is successful!"}
    except Exception as e:
        return {"status": "Database connection failed", "error": str(e)}