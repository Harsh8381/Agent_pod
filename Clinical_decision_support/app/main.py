from fastapi import FastAPI

from app import vbc_poc
from app.api.routes import router


def create_app() -> FastAPI:
    application = FastAPI(
        title="Clinical Decision Support API",
        description="Decision-support suggestions grounded in the local medical knowledge base.",
        version="1.0.0",
    )
    application.include_router(router)
    if vbc_poc.enabled():  # feature flag: VBC_POC_ENABLED=0 removes the POC without touching the encounter flow
        from app.api.vbc_routes import router as vbc_router

        application.include_router(vbc_router)
    return application


app = create_app()