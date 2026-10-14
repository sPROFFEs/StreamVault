import os
from fastapi.templating import Jinja2Templates

# Usar ruta absoluta para evitar errores en recarga
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

if not os.path.exists(TEMPLATES_DIR):
     TEMPLATES_DIR = "/app/templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
