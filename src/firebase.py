import firebase_admin
from firebase_admin import credentials, firestore_async
from firedantic import configure
from google.cloud.firestore_v1.async_client import AsyncClient
from src.config import settings

firebaseDatabase: AsyncClient | None = None


async def init_firebase() -> None:
    global firebaseDatabase
    cred = credentials.Certificate({
        "type": "service_account",
        "project_id": settings.FIREBASE_PROJECT_ID,
        "private_key_id": "",
        "private_key": settings.FIREBASE_PRIVATE_KEY.replace("\\n", "\n"),
        "client_email": settings.FIREBASE_CLIENT_EMAIL,
        "client_id": "",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    })
    firebase_admin.initialize_app(cred)
    firebaseDatabase = firestore_async.client()
    configure(firebaseDatabase)
    print("Firebase connected")


async def close_firebase() -> None:
    global firebaseDatabase
    firebaseDatabase = None
    try:
        app = firebase_admin.get_app()
    except ValueError:
        return
    firebase_admin.delete_app(app)
