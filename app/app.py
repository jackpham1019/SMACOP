import os
import httpx
import time
import logging
import psycopg2
import hashlib
from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from exceptions import DatabaseConnectionError, LogCreationError, UserRegistrationError, InvalidCredentialsError
from jwt_key import create_access_token, decode_and_verify_token
# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("web-app")

app = FastAPI(title="Project 1")

# Read Database connection details from Environment Variables
DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "secret")
DB_AUTH_MODE = os.getenv("DB_AUTH_MODE", "sql-auth").lower()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db_connection():
    logger.info("Using standard username/password (SQL Auth) credentials")
    db_password = DB_PASSWORD
    
    try:
        # Establish PostgreSQL connection
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=db_password,
            # For Azure SQL/PostgreSQL, SSL is typically required
            sslmode="require" if DB_AUTH_MODE == "azure-ad" else "prefer",
            connect_timeout=5 # 5 seconds connection timeout
        )
        _ensure_table_exists()
        logger.info("Database connection successfully established")
        return conn
    except psycopg2.OperationalError as e:
        logger.error(f"Database connection error: {str(e)}")
        raise e

#Ensure user data Exists 
def _ensure_table_exists():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute
        cursor.execute("""
                CREATE TABLE IF NOT EXISTS application_users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    hashed_password VARCHAR(64) NOT NULL
                );
            """)
        connection.commit()
        logger.info("User table exists")
    except Exception as err:
        logging.critical(f"Schemea verification failed: {err}")
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

#Create a user to register
def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def create_user(username: str, password: str):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        hashed = _hash_password(password)

        cursor.execute(
            "INSERT INTO application_users (username, hashed_password) VALUES (%s, %s);",
            (username, hashed)
        )
        connection.commit()
    except psycopg2.IntegrityError:
        raise psycopg2.IntegrityError("Username is already registered inside the domain")
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

def authenticate_user(username: str, password: str) -> bool:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        hashed = _hash_password(password)

        cursor.execute(
            "SELECT id FROM application_users WHERE username = %s AND hashed_password = %s;",
            (username, hashed)
         )
        user_record = cursor.fetchone()
        if not user_record:
            raise InvalidCredentialsError("Invalid username or password validation cred")
        return True
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

class AuthPayload(BaseModel):
    username: str = Field(..., examples=["engineer_alpha"])
    password: str = Field(..., min_length=6, examples=["supersecret123"])

class TokenResponse(BaseModel):
    access_token: str
    token_type: str


@app.middleware("http")
async def log_request_execution_latency(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    logger.info(f"HTTP {request.method} {request.url.path} processed in {time.time() - start_time:.4f}s")
    return response

@app.get("/")
def read_root():
    return{
        "status": "Online",
        "configuration": {
            "db_host": DB_HOST,
            "db_port": DB_PORT,
            "db_name": DB_NAME,
            "db_user": DB_USER,
            "auth_mode": DB_AUTH_MODE,
        },
        "description": "lorem Ipsum"
    }

@app.post("/register", status_code=201)
async def register(payload: AuthPayload):
    try:
        create_user(username=payload.username, password=payload.password)
        return {
            "status": "success",
            "detail": f"Account for user `{payload.username}` has been created"
                }
    except UserRegistrationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    

@app.post("/login")
async def login(payload: AuthPayload):
    try:
        authenticate_user(username=payload.username,password=payload.password)
        jwt_token = create_access_token(username=payload.username)
        return TokenResponse(access_token=jwt_token, token_type="bearer")
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=401, detail=str(e))

@app.get("/login-check")
async def login_check(username: str = Depends(decode_and_verify_token)):
    async with httpx.AsyncClient() as client:
        response = await client.get("https://p1-web-app.azurewebsites.net/health")
    return {"status_code": response.status_code, "body": response.text} 
        

@app.get("/health")
def health_check():
    return  {"status": "healthy"}

@app.get("/db-check")
def db_check():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        db_version = cursor.fetchone()
        cursor.close()

        return {
            "database_connection": "SUCCESS",
            "postgres_version": db_version[0],
            "auth_method_used": DB_AUTH_MODE
        }
    except Exception as e:
        logger.error(f"Failed database connectivity verification: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "database_connection": "FAILED",
                "reason": str(e)
            }
        )
    finally:
        if conn:
            conn.close()


