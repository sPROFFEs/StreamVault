"""
Transcoder Service - Procesamiento de video con FFmpeg

Transcodifica videos a múltiples calidades para streaming adaptativo.
"""
import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Quality(Enum):
    """Perfiles de calidad predefinidos"""
    SD_480 = ("480p", 854, 480, "1M", "128k")
    HD_720 = ("720p", 1280, 720, "2.5M", "192k")
    HD_1080 = ("1080p", 1920, 1080, "5M", "256k")
    UHD_4K = ("4k", 3840, 2160, "15M", "384k")
    
    def __init__(self, label: str, width: int, height: int, 
                 video_bitrate: str, audio_bitrate: str):
        self.label = label
        self.width = width
        self.height = height
        self.video_bitrate = video_bitrate
        self.audio_bitrate = audio_bitrate


@dataclass
class TranscodeJob:
    """Representa un trabajo de transcodificación"""
    id: str
    input_path: str
    output_dir: str
    qualities: List[Quality]
    status: str = "pending"
    progress: float = 0.0
    error: Optional[str] = None
    outputs: Dict[str, str] = None
    
    def __post_init__(self):
        if self.outputs is None:
            self.outputs = {}


class Transcoder:
    """Servicio de transcodificación con FFmpeg"""
    
    # Directorio base para salidas
    OUTPUT_BASE = "/saas-data/transcoded"
    
    def __init__(self, output_base: str = None):
        self.output_base = Path(output_base or self.OUTPUT_BASE)
        self.output_base.mkdir(parents=True, exist_ok=True)
        self.jobs: Dict[str, TranscodeJob] = {}
        
        # Verificar FFmpeg
        self.ffmpeg_available = self._check_ffmpeg()
        if not self.ffmpeg_available:
            logger.error("FFmpeg no encontrado. Transcoder deshabilitado.")
    
    def _check_ffmpeg(self) -> bool:
        """Verifica que FFmpeg esté instalado"""
        return shutil.which("ffmpeg") is not None
    
    async def transcode(self, 
                        input_path: str,
                        qualities: List[Quality] = None,
                        output_name: str = None) -> TranscodeJob:
        """
        Transcodifica un video a múltiples calidades.
        
        Args:
            input_path: Ruta al video de entrada (puede ser URL)
            qualities: Lista de calidades a generar (default: 480p, 720p, 1080p)
            output_name: Nombre base para los archivos de salida
        
        Returns:
            TranscodeJob con información del trabajo
        """
        if not self.ffmpeg_available:
            raise RuntimeError("FFmpeg no disponible")
        
        # Defaults
        qualities = qualities or [Quality.SD_480, Quality.HD_720, Quality.HD_1080]
        
        # Generar ID y nombre
        import uuid
        job_id = str(uuid.uuid4())[:8]
        
        if not output_name:
            if input_path.startswith("http"):
                output_name = f"stream_{job_id}"
            else:
                output_name = Path(input_path).stem
        
        output_dir = self.output_base / output_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Crear job
        job = TranscodeJob(
            id=job_id,
            input_path=input_path,
            output_dir=str(output_dir),
            qualities=qualities
        )
        self.jobs[job_id] = job
        
        # Ejecutar en background
        asyncio.create_task(self._run_transcode(job))
        
        return job
    
    async def _run_transcode(self, job: TranscodeJob):
        """Ejecuta la transcodificación en background"""
        job.status = "running"
        
        try:
            for i, quality in enumerate(job.qualities):
                output_path = Path(job.output_dir) / f"{quality.label}.mp4"
                
                logger.info(f"Transcodificando a {quality.label}...")
                
                cmd = self._build_ffmpeg_command(
                    job.input_path)
                cmd.extend([
                    "-vf", f"scale={quality.width}:{quality.height}",
                    "-b:v", quality.video_bitrate,
                    "-b:a", quality.audio_bitrate,
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-threads", "2",
                    "-c:a", "aac",
                    "-movflags", "+faststart",
                    "-y",
                    str(output_path)
                ])
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                _, stderr = await process.communicate()
                
                if process.returncode != 0:
                    raise RuntimeError(f"FFmpeg error: {stderr.decode()[-500:]}")
                
                job.outputs[quality.label] = str(output_path)
                job.progress = (i + 1) / len(job.qualities) * 100
            
            # Generar playlist HLS si hay múltiples calidades
            if len(job.qualities) > 1:
                await self._generate_hls_master(job)
            
            job.status = "completed"
            logger.info(f"Job {job.id} completado")
            
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            logger.error(f"Job {job.id} falló: {e}")
    
    def _build_ffmpeg_command(self, input_path: str) -> List[str]:
        """Construye comando base de FFmpeg"""
        cmd = ["ffmpeg"]
        
        # Si es URL, añadir headers anti-bot
        if input_path.startswith("http"):
            cmd.extend([
                "-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5"
            ])
        
        cmd.extend(["-i", input_path])
        return cmd
    
    async def _generate_hls_master(self, job: TranscodeJob):
        """Genera playlist HLS master con todas las calidades"""
        master_path = Path(job.output_dir) / "master.m3u8"
        
        lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
        
        for quality in job.qualities:
            if quality.label in job.outputs:
                bandwidth = int(quality.video_bitrate.replace("M", "000000").replace("k", "000"))
                lines.append(
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={quality.width}x{quality.height}"
                )
                lines.append(f"{quality.label}.mp4")
        
        master_path.write_text("\n".join(lines))
        job.outputs["master"] = str(master_path)
    
    async def download_and_transcode(self, url: str, 
                                     output_name: str = None) -> TranscodeJob:
        """
        Descarga un stream y lo transcodifica.
        Útil para cachear contenido de fuentes externas.
        """
        return await self.transcode(url, output_name=output_name)
    
    def get_job_status(self, job_id: str) -> Optional[TranscodeJob]:
        """Obtiene el estado de un job"""
        return self.jobs.get(job_id)
    
    def list_transcoded(self) -> List[Dict]:
        """Lista todos los contenidos transcodificados"""
        results = []
        for item in self.output_base.iterdir():
            if item.is_dir():
                qualities = []
                for f in item.glob("*.mp4"):
                    qualities.append(f.stem)
                
                results.append({
                    "name": item.name,
                    "path": str(item),
                    "qualities": qualities,
                    "has_master": (item / "master.m3u8").exists()
                })
        return results
    
    async def extract_audio(self, input_path: str, output_path: str = None) -> str:
        """Extrae solo el audio de un video"""
        if not output_path:
            output_path = str(Path(input_path).with_suffix(".mp3"))
        
        cmd = [
            "ffmpeg", "-i", input_path,
            "-vn", "-acodec", "libmp3lame",
            "-q:a", "2",
            "-y", output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        return output_path
    
    async def get_video_info(self, input_path: str) -> Optional[Dict]:
        """Obtiene información del video usando ffprobe"""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            input_path
        ]
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            
            import json
            return json.loads(stdout.decode())
        except Exception as e:
            logger.error(f"Error obteniendo info de video: {e}")
            return None
