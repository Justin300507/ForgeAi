def build_backend_prompt(architecture):
    return f"""
You are ForgeAI Backend Generator.

Architecture:

{architecture}

Generate a complete FastAPI backend.

Return ONLY valid JSON.

Format:

{{
    "files": [
        {{
            "path": "",
            "content": ""
        }}
    ]
}}

REQUIRED FILES

app/main.py
app/requirements.txt
app/__init__.py
app/routes/__init__.py
app/models/__init__.py
app/services/__init__.py

OPTIONAL FILES

app/routes/<module>_routes.py
app/models/<module>.py
app/services/<module>_service.py

OUTPUT RULES

- JSON only
- No markdown
- No explanations
- No code fences
- Maximum 8 files
- Every file under 25 lines
- Prioritize valid JSON over completeness

PATH RULES

- Paths must start with app/
- Paths must use forward slashes
- Paths must contain ASCII characters only
- Allowed extensions: .py .txt

VALID IMPORTS

from app.routes.user_routes import user_router
from app.models.user import User
from app.services.user_service import create_user

INVALID IMPORTS

from routes.user_routes import user_router
from models.user import User
from services.user_service import create_user

FASTAPI RULES

- Use FastAPI
- Use APIRouter
- Use Pydantic
- Use absolute imports beginning with app
- No Flask
- No Django

DEPENDENCIES

Include all required packages in requirements.txt.

Examples:

fastapi
uvicorn
pydantic
email-validator
python-multipart

CONSISTENCY RULES

- Every imported file must exist
- Every imported function must exist
- Every imported class must exist
- Every router must exist
- No broken imports
- No circular imports

RUNTIME GOAL

The backend must start successfully with:

python -m uvicorn app.main:app

Return JSON only.
"""