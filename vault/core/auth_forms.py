"""Fixed, localized authentication notices and progressive form responses."""
from urllib.parse import urlencode

from fastapi.responses import JSONResponse, RedirectResponse


MESSAGES = {
    "invalid_credentials": ("Invalid username or password. Check your details and try again.", "Usuario o contraseña incorrectos. Revisa tus datos e inténtalo de nuevo."),
    "blocked": ("Too many attempts. Wait 15 minutes before trying again.", "Demasiados intentos. Espera 15 minutos antes de volver a intentarlo."),
    "invalid_username": ("Use 3–32 letters, numbers, dots, dashes or underscores. Start with a letter or number; avoid consecutive dots.", "Usa de 3 a 32 letras, números, puntos, guiones o guiones bajos. Empieza con una letra o número; evita puntos consecutivos."),
    "weak_password": ("Use at least 8 characters, with uppercase and lowercase letters, a number and a symbol (!@#$%^&*(),.?\":{}|<>).", "Usa al menos 8 caracteres, con mayúsculas, minúsculas, un número y un símbolo (!@#$%^&*(),.?\":{}|<>)."),
    "configured": ("StreamVault is already configured. Sign in with your existing account.", "StreamVault ya está configurado. Entra con tu cuenta existente."),
    "action_not_allowed": ("Account creation is unavailable. Sign in with your existing account.", "No se pueden crear más cuentas aquí. Entra con tu cuenta existente."),
    "created": ("Administrator account created. You can sign in now.", "Cuenta de administrador creada. Ya puedes iniciar sesión."),
    "username_required": ("Enter your username.", "Introduce tu usuario."),
    "password_required": ("Enter your password.", "Introduce tu contraseña."),
    "network": ("Unable to connect. Check your connection and try again.", "No se pudo conectar. Revisa tu conexión e inténtalo de nuevo."),
    "unavailable": ("Unable to sign in right now. Please try again.", "No se puede iniciar sesión ahora. Inténtalo de nuevo."),
}

# Accept existing bookmarked notices, but never display arbitrary query values.
LEGACY_CODES = {
    "Invalid username or password": "invalid_credentials",
    "Temporarily blocked for security reasons (15 min).": "blocked",
    "Invalid username": "invalid_username",
    "Password is too weak": "weak_password",
    "The system is already configured": "configured",
    "Action not allowed": "action_not_allowed",
    "Admin account created successfully. You can sign in now.": "created",
}


def wants_json(request):
    return "application/json" in request.headers.get("accept", "")


def form_context(request):
    lang = "es" if request.cookies.get("lang") == "es" else "en"
    messages = {code: values[lang == "es"] for code, values in MESSAGES.items()}
    raw_code = request.query_params.get("notice") or request.query_params.get("error", "")
    code = LEGACY_CODES.get(raw_code, raw_code)
    return {"lang": lang, "messages": messages, "notice": messages.get(code, ""),
            "notice_success": code == "created"}


def failure(request, code, path="/login", next_target="", status_code=400):
    if wants_json(request):
        return JSONResponse({"code": code}, status_code=status_code)
    query = {"error": code}
    if next_target:
        query["next"] = next_target
    return RedirectResponse(f"{path}?{urlencode(query)}", status_code=303)


def success(request, target):
    if wants_json(request):
        return JSONResponse({"redirect": target})
    return RedirectResponse(target, status_code=303)
