import logging
import os
import time

os.environ.setdefault("OTEL_SERVICE_NAME", "account-service")

from azure.monitor.opentelemetry import configure_azure_monitor

configure_azure_monitor()

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from pydantic import BaseModel

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger("account-service")

app = FastAPI(title="Account Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AccountCreateRequest(BaseModel):
    account_id: str

@app.middleware("http")
async def log_request_execution_latency(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    logger.info(f"HTTP {request.method} {request.url.path} processed in {time.time() - start_time:.4f}s")
    return response

@app.post("/accounts", status_code=201)
def create_account(payload: AccountCreateRequest):

    pass
    # span = trace.get_current_span()

    # if span.is_recording():
    #     span.set_attribute("app.journey", "account-create")

    # connection = None
    # cursor = None

    # try:
    #     connection = get_db_connection()
    #     cursor = connection.cursor()

    #     cursor.execute(
    #         """
    #         INSERT INTO accounts (
    #             account_id,
    #             balance_cents
    #         )
    #         VALUES (%s, 0);
    #         """,
    #         (payload.account_id,),
    #     )

    #     connection.commit()

    #     return {
    #         "account_id": payload.account_id,
    #         "balance_cents": 0,
    #     }

    # except errors.UniqueViolation:
    #     if connection:
    #         connection.rollback()

    #     raise HTTPException(
    #         status_code=409,
    #         detail="Account already exists",
    #     )

    # except psycopg2.Error:
    #     if connection:
    #         connection.rollback()

    #     logger.exception("Account creation failed")

    #     raise HTTPException(
    #         status_code=500,
    #         detail="Account creation failed",
    #     )

    # finally:
    #     if cursor:
    #         cursor.close()

    #     if connection:
    #         connection.close()

@app.get("/accounts")
def list_accounts():
    # connection = None
    # cursor = None

    # try:
    #     connection = get_db_connection()
    #     cursor = connection.cursor()

    #     cursor.execute(
    #         """
    #         SELECT account_id, balance_cents, created_at
    #         FROM accounts
    #         ORDER BY created_at;
    #         """
    #     )

    #     rows = cursor.fetchall()

    #     return [
    #         {
    #             "account_id": row[0],
    #             "balance_cents": row[1],
    #             "created_at": row[2],
    #         }
    #         for row in rows
    #     ]

    # finally:
    #     if cursor:
    #         cursor.close()

    #     if connection:
    #         connection.close()
    return [
        {
            "account_id": 1,
            "balance_cents": 1000000,
            "created_at": "just now"
        }
    ]

@app.get("/accounts/{account_id}/balance")
def get_account_balance(account_id: str):

    try:
        # connection = get_db_connection()
        # cursor = connection.cursor()

        # cursor.execute(
        #     """
        #     SELECT balance_cents
        #     FROM accounts
        #     WHERE account_id = %s;
        #     """,
        #     (account_id,),
        # )

        # row = cursor.fetchone()

        # if row is None:
        #     raise HTTPException(
        #         status_code=404,
        #         detail="Account not found",
        #     )

        return {
            "account_id": "my account",
            "balance_cents": 9999999,
            "balance": 99999.99,
        }

    finally:
        # if cursor:
        #     cursor.close()

        # if connection:
        #     connection.close()
        pass


@app.get("/")
def read_root():
    return {
        "status": "Web App Online",
        "description": "with OpenTelemetry and App Insights!"
    }

@app.get("/health")
def health_check():
    return  {"status": "healthy"}
