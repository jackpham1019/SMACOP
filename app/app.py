import hashlib
import logging
import os
import time

# Set before configuring OpenTelemetry.
os.environ.setdefault("OTEL_SERVICE_NAME", "auth-service")

from exceptions import InvalidCredentialsError, UserRegistrationError
from azure.monitor.opentelemetry import configure_azure_monitor

configure_azure_monitor(
    connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
)

# Import instrumented libraries after Azure Monitor configuration.
import httpx
import psycopg2
from psycopg2 import errors
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

HTTPXClientInstrumentor().instrument()

from jwt_key import CurrentUser, create_access_token, decode_and_verify_token, require_admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger("auth-service")

app = FastAPI(title="Bank Auth Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "secret")
DB_AUTH_MODE = os.getenv("DB_AUTH_MODE", "sql-auth").lower()

ACCOUNT_SERVICE_URL = os.getenv(
    "ACCOUNT_SERVICE_URL",
    "http://localhost:8001",
)

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
        logger.info("Database connection successfully established")
        return conn
    except psycopg2.OperationalError as e:
        logger.error(f"Database connection error: {str(e)}")
        raise e

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

#Ensure user data Exists 
def _ensure_table_exists():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS application_users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                hashed_password VARCHAR(64) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'user',
                CONSTRAINT valid_user_role
                    CHECK (role IN ('user', 'admin'))
            );
            """
        )

        hashed = _hash_password("password")

        cursor.execute(
            """
            INSERT INTO application_users (
                username,
                hashed_password,
                role
            )
            VALUES ('admin', %s, 'admin')
            ON CONFLICT (username) DO NOTHING;
            """,
            (hashed,),
        )

        connection.commit()
        logger.info("User table and admin account exist")
    except Exception as err:
        logging.critical(f"Schemea verification failed: {err}")
    finally:
        if cursor: cursor.close()
        if connection: connection.close()


@app.on_event("startup")
def startup() -> None:
    _ensure_table_exists()


# Create a user to register
def create_user(username: str, password: str):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        hashed = _hash_password(password)

        cursor.execute(
            """
            INSERT INTO application_users (
                username,
                hashed_password,
                role
            )
            VALUES (%s, %s, 'user');
            """,
            (username, hashed),
        )

        connection.commit()
    except psycopg2.IntegrityError:
        raise UserRegistrationError("Username is already registered inside the domain")
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
            """
            SELECT username, role
            FROM application_users
            WHERE username = %s
              AND hashed_password = %s;
            """,
            (username, hashed)
        )
        user_record = cursor.fetchone()
        if not user_record:
            raise InvalidCredentialsError("Invalid username or password validation cred")
        return {
            "username": user_record[0],
            "role": user_record[1],
        }
    finally:
        if cursor: cursor.close()
        if connection: connection.close()

async def forward_account_request(
    method: str,
    path: str,
    json_body: dict | None = None,
):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.request(
                method=method,
                url=f"{ACCOUNT_SERVICE_URL}{path}",
                json=json_body,
            )

        response.raise_for_status()
        return response.json()

    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get(
                "detail",
                "Account service request failed",
            )
        except ValueError:
            detail = "Account service request failed"

        raise HTTPException(
            status_code=exc.response.status_code,
            detail=detail,
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Account service is unavailable",
        )
    
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


@app.post("/register", status_code=201)
async def register(payload: AuthPayload):
    span = trace.get_current_span()

    if span.is_recording():
        span.set_attribute("app.journey", "user-register")

    try:
        create_user(
            username=payload.username,
            password=payload.password,
        )

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{ACCOUNT_SERVICE_URL}/accounts",
                    json={
                        "account_id": payload.username,
                    },
                )

            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Account service rejected account creation: %s",
                exc.response.text,
            )

            raise HTTPException(
                status_code=502,
                detail="User was created, but account creation failed",
            )

        except httpx.RequestError:
            logger.exception("Account service is unavailable")

            raise HTTPException(
                status_code=503,
                detail="Account service is unavailable",
            )

        return {
            "status": "success",
            "detail": f"Account for user `{payload.username}` has been created"
        }
    except UserRegistrationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    

@app.post("/login")
def login(payload: AuthPayload):
    span = trace.get_current_span()

    if span.is_recording():
        span.set_attribute("app.journey", "user-login")

    try:
        user = authenticate_user(
            username=payload.username,
            password=payload.password,
        )

        jwt_token = create_access_token(
            username=user["username"],
            role=user["role"]
        )
        return TokenResponse(access_token=jwt_token, token_type="bearer")
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=401, detail=str(e))

@app.get("/balance")
async def check_balance(
    current_user: CurrentUser = Depends(decode_and_verify_token),
):
    span = trace.get_current_span()

    if span.is_recording():
        span.set_attribute("app.journey", "check-balance")

    return await forward_account_request(
        method="GET",
        path=f"/accounts/{current_user.username}/balance",
    )

@app.get("/accounts")
async def list_accounts(
    current_user: CurrentUser = Depends(require_admin),
):
    return await forward_account_request(
        method="GET",
        path="/accounts"
    )

@app.get("/health")
def health_check():
    return  {"status": "healthy"}

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

# @app.get("/db-check")
# def db_check():
#     conn = None
#     try:
#         conn = get_db_connection()
#         cursor = conn.cursor()
#         cursor.execute("SELECT version();")
#         db_version = cursor.fetchone()
#         cursor.close()

#         return {
#             "database_connection": "SUCCESS",
#             "postgres_version": db_version[0],
#             "auth_method_used": DB_AUTH_MODE
#         }
#     except Exception as e:
#         logger.error(f"Failed database connectivity verification: {str(e)}")
#         raise HTTPException(
#             status_code=500,
#             detail={
#                 "database_connection": "FAILED",
#                 "reason": str(e)
#             }
#         )
#     finally:
#         if conn:
#             conn.close()


