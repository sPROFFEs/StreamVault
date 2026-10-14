import time
import ipaddress
import socket
from urllib.parse import urlparse
from fastapi import Request, HTTPException
import logging

logger = logging.getLogger("security")

# ALMACÉN EN MEMORIA (Volátil)
# Estructura: { "IP_ADDR": { "attempts": 0, "block_until": timestamp, "last_seen": timestamp } }
login_attempts = {}

# --- CONFIGURACIÓN DE DUREZA ---
MAX_ATTEMPTS = 5          # Fallos permitidos antes del ban
BLOCK_TIME = 900          # Tiempo de castigo: 15 minutos (900s)
RESET_TIME = 300          # Tiempo para olvidar fallos previos: 5 minutos

INTERNAL_DOCKER_HOSTNAMES = {
    "core", "downloader", "cloudflared", "nginx", "localhost", "host.docker.internal"
}


def is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        return False


def get_real_ip(request: Request) -> str:
    """
    Obtiene la IP real del usuario autenticado o entrante.
    Si el peer inmediato es un proxy interno (red privada o docker bridge),
    aplica encabezados confiables como CF-Connecting-IP (Cloudflare) o X-Real-IP (Nginx).
    """
    peer_ip = request.client.host if request.client else "127.0.0.1"
    
    if is_private_ip(peer_ip):
        cloudflare_ip = request.headers.get("CF-Connecting-IP")
        if cloudflare_ip and request.headers.get("CF-Ray"):
            return cloudflare_ip.strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
            
    return peer_ip


def is_safe_external_url(url: str) -> bool:
    """
    SSRF Protection: Verifica que una URL remota apunte únicamente a un servicio
    público válido y no a la red interna de Docker, metadatos de nube o loopback.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        
        lower_host = hostname.lower()
        if lower_host in INTERNAL_DOCKER_HOSTNAMES or lower_host.endswith(".local") or lower_host.endswith(".internal"):
            return False

        # Check if direct IP literal
        if is_private_ip(lower_host):
            return False

        # Resolve DNS to check resulting IP addresses
        addr_info = socket.getaddrinfo(hostname, None)
        for entry in addr_info:
            resolved_ip = entry[4][0]
            if is_private_ip(resolved_ip):
                return False

        return True
    except Exception as exc:
        logger.warning("SSRF check blocked suspect URL '%s': %s", url[:120], exc)
        return False


def check_brute_force(request: Request):
    """
    Se llama ANTES de procesar el login. Si está baneado, lanza error 429.
    """
    client_ip = get_real_ip(request)
    now = time.time()
    
    record = login_attempts.get(client_ip)
    
    if not record:
        return # Limpio
    
    # 1. ¿Está la IP actualmente en la cárcel?
    if record["block_until"] > now:
        remaining = int(record["block_until"] - now)
        logger.warning(f"IP Bloqueada {client_ip} intentó entrar. Restan {remaining}s")
        raise HTTPException(
            status_code=429, 
            detail=f"Demasiados intentos. IP bloqueada por seguridad. Espera {int(remaining/60)} minutos."
        )

    # 2. Si ha pasado el tiempo de reset sin ser bloqueado, limpiamos el historial
    if record["block_until"] == 0 and (now - record["last_seen"] > RESET_TIME):
        del login_attempts[client_ip]


def register_failed_attempt(request: Request) -> bool:
    """
    Se llama cuando falla la contraseña. Retorna True si acaba de ser bloqueado.
    """
    client_ip = get_real_ip(request)
    now = time.time()
    
    if client_ip not in login_attempts:
        login_attempts[client_ip] = {"attempts": 0, "block_until": 0, "last_seen": now}
    
    record = login_attempts[client_ip]
    record["attempts"] += 1
    record["last_seen"] = now
    
    logger.info(f"Fallo de login desde {client_ip} ({record['attempts']}/{MAX_ATTEMPTS})")
    
    # APLICAR MARTILLO
    if record["attempts"] >= MAX_ATTEMPTS:
        record["block_until"] = now + BLOCK_TIME
        logger.warning(f"⛔ IP BLOQUEADA: {client_ip} por {BLOCK_TIME} segundos.")
        return True
    
    return False


def clear_attempts(request: Request):
    """
    Se llama cuando el login es EXITOSO. Borra el historial.
    """
    client_ip = get_real_ip(request)
    if client_ip in login_attempts:
        del login_attempts[client_ip]

