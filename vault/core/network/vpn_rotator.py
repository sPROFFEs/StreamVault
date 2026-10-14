import os
import subprocess
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class WireGuardRotator:
    """Rotador automático de configuraciones WireGuard"""
    
    def __init__(self, 
                 config_dir: str = "/saas-data/wireguard",
                 rotation_interval_minutes: int = 30):
        self.config_dir = Path(config_dir)
        self.rotation_interval = timedelta(minutes=rotation_interval_minutes)
        self.current_config: Optional[str] = None
        self.last_rotation: Optional[datetime] = None
        self.configs: List[str] = []
        
        # Ensure dir exists
        if not self.config_dir.exists():
            logger.warning(f"WireGuard config dir {config_dir} not found. Creating it.")
            self.config_dir.mkdir(parents=True, exist_ok=True)
            
    def _load_configs(self):
        """Carga lista de configuraciones .conf disponibles"""
        self.configs = sorted([
            f.name for f in self.config_dir.glob("*.conf")
        ])
        logger.info(f"Loaded {len(self.configs)} WireGuard configs")
    
    def _get_next_config(self) -> str:
        """Obtiene siguiente config en rotación circular"""
        if not self.configs:
            self._load_configs()
            if not self.configs:
                raise ValueError(f"No WireGuard configs found in {self.config_dir}")
                
        if not self.current_config:
            return self.configs[0]
        
        try:
            idx = self.configs.index(self.current_config)
            next_idx = (idx + 1) % len(self.configs)
            return self.configs[next_idx]
        except ValueError:
            # Current config might have been deleted
            return self.configs[0]
    
    async def connect(self, config_name: str = None) -> bool:
        """Conecta a una configuración específica o la siguiente"""
        try:
            if not self.configs:
                 self._load_configs()
            
            config = config_name or self._get_next_config()
            config_path = self.config_dir / config
            
            logger.info(f"Connecting to WireGuard config: {config}")
            
            # Desconectar actual si existe
            if self.current_config:
                await self.disconnect()
            
            # Conectar usando wg-quick
            # Requiere NET_ADMIN capability en el container
            result = await asyncio.create_subprocess_exec(
                "wg-quick", "up", str(config_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            
            if result.returncode == 0:
                self.current_config = config
                self.last_rotation = datetime.now()
                logger.info(f"Successfully connected to {config}")
                return True
            else:
                logger.error(f"Failed to connect to {config}: {stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"Error rotating VPN: {e}")
            return False
    
    async def disconnect(self) -> bool:
        """Desconecta VPN actual"""
        if not self.current_config:
            return True
        
        try:
            config_path = self.config_dir / self.current_config
            logger.info(f"Disconnecting from {self.current_config}...")
            
            result = await asyncio.create_subprocess_exec(
                "wg-quick", "down", str(config_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            
            if result.returncode == 0:
                self.current_config = None
                return True
            else:
                # A veces falla si ya estaba abajo, pero asumimos desconectado
                logger.warning(f"Disconnect warning: {stderr.decode()}")
                self.current_config = None
                return True
        except Exception as e:
            logger.error(f"Error disconnecting: {e}")
            return False
    
    async def rotate_if_needed(self) -> bool:
        """Rota a siguiente config si pasó el intervalo"""
        if not self.configs:
            self._load_configs()
            if not self.configs:
                return False
                
        if not self.last_rotation:
            return await self.connect()
        
        if datetime.now() - self.last_rotation >= self.rotation_interval:
            return await self.connect()
        return False
    
    def get_status(self) -> Dict:
        """Estado actual de la VPN"""
        return {
            "connected": self.current_config is not None,
            "current_config": self.current_config,
            "last_rotation": self.last_rotation.isoformat() if self.last_rotation else None,
            "next_rotation": (self.last_rotation + self.rotation_interval).isoformat() 
                            if self.last_rotation else None,
            "available_configs": len(self.configs)
        }

# Background task para rotación automática
async def vpn_rotation_scheduler(rotator: WireGuardRotator):
    """Scheduler que corre en background"""
    logger.info("Starting VPN rotation scheduler")
    while True:
        try:
            await rotator.rotate_if_needed()
        except Exception as e:
            logger.error(f"Error in VPN scheduler: {e}")
        
        await asyncio.sleep(60)  # Check cada minuto
