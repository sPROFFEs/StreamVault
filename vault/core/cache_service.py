"""
StreamVault Cache Service

Handles:
- Download-while-streaming
- Storage management
- Cache cleanup
"""

import os
import asyncio
import aiohttp
import aiofiles
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session

import database
from config import settings

logger = logging.getLogger(__name__)


class StorageStats:
    """Storage usage statistics"""
    def __init__(self, used_bytes: int, limit_bytes: int):
        self.used_bytes = used_bytes
        self.limit_bytes = limit_bytes
    
    @property
    def used_gb(self) -> float:
        return round(self.used_bytes / (1024**3), 2)
    
    @property
    def limit_gb(self) -> float:
        return round(self.limit_bytes / (1024**3), 2)
    
    @property
    def free_bytes(self) -> int:
        return max(0, self.limit_bytes - self.used_bytes)
    
    @property
    def free_gb(self) -> float:
        return round(self.free_bytes / (1024**3), 2)
    
    @property
    def percent_used(self) -> float:
        if self.limit_bytes == 0:
            return 0
        return round((self.used_bytes / self.limit_bytes) * 100, 1)


class CacheService:
    """
    Service for caching streamed content in background.
    """
    
    def __init__(self, cache_path: str = None):
        self.cache_path = Path(cache_path or settings.MEDIA_STORAGE_PATH)
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self._active_downloads: dict[str, asyncio.Task] = {}
    
    def get_cache_path_for_media(self, source_provider: str, source_id: str, 
                                  season: int = None, episode: int = None) -> Path:
        """Generate cache file path for a media item"""
        if season is not None and episode is not None:
            # Series episode
            filename = f"{source_id}_s{season:02d}e{episode:02d}.mp4"
            return self.cache_path / source_provider / "series" / source_id / filename
        else:
            # Movie
            filename = f"{source_id}.mp4"
            return self.cache_path / source_provider / "movies" / filename
    
    def is_cached(self, source_provider: str, source_id: str,
                  season: int = None, episode: int = None) -> bool:
        """Check if content is already cached"""
        path = self.get_cache_path_for_media(source_provider, source_id, season, episode)
        return path.exists() and path.stat().st_size > 0
    
    def get_cached_path(self, source_provider: str, source_id: str,
                        season: int = None, episode: int = None) -> Optional[Path]:
        """Get path to cached file if exists"""
        path = self.get_cache_path_for_media(source_provider, source_id, season, episode)
        if path.exists() and path.stat().st_size > 0:
            return path
        return None
    
    async def stream_and_cache(self, source_url: str, local_path: Path,
                               headers: dict = None, 
                               progress_callback=None) -> bool:
        """
        Download content while also allowing streaming.
        Returns True if successful.
        """
        cache_key = str(local_path)
        
        if cache_key in self._active_downloads:
            logger.info(f"Download already in progress: {local_path.name}")
            return True
        
        local_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = local_path.with_suffix('.part')
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(source_url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"Failed to fetch stream: {response.status}")
                        return False
                    
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    
                    async with aiofiles.open(temp_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(settings.STREAM_BUFFER_SIZE):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            
                            if progress_callback and total_size:
                                progress = int((downloaded / total_size) * 100)
                                await progress_callback(progress)
            
            # Rename temp to final
            temp_path.rename(local_path)
            logger.info(f"Cached: {local_path.name} ({downloaded / (1024*1024):.1f} MB)")
            return True
            
        except Exception as e:
            logger.error(f"Cache error: {e}")
            if temp_path.exists():
                temp_path.unlink()
            return False
        
        finally:
            self._active_downloads.pop(cache_key, None)
    
    def start_background_cache(self, db: Session, media_item_id: int,
                                source_url: str, local_path: Path,
                                episode_id: int = None):
        """Start caching in background and track in DB"""
        # Create job record
        job = database.CacheJob(
            media_item_id=media_item_id,
            episode_id=episode_id,
            source_url=source_url,
            target_path=str(local_path),
            status="downloading"
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        
        job_id = job.id
        cache_key = str(local_path)
        
        async def _download_task():
            db_session = database.SessionLocal()
            try:
                async def update_progress(percent):
                    j = db_session.query(database.CacheJob).get(job_id)
                    if j:
                        j.progress_percent = percent
                        db_session.commit()
                
                success = await self.stream_and_cache(
                    source_url, local_path,
                    progress_callback=update_progress
                )
                
                j = db_session.query(database.CacheJob).get(job_id)
                if j:
                    j.status = "completed" if success else "failed"
                    j.completed_at = datetime.now(timezone.utc)
                    db_session.commit()
                
                # Update media item
                if success:
                    if episode_id:
                        ep = db_session.query(database.Episode).get(episode_id)
                        if ep:
                            ep.local_path = str(local_path)
                            ep.cached_at = datetime.now(timezone.utc)
                            ep.cache_size_bytes = local_path.stat().st_size
                    else:
                        mi = db_session.query(database.MediaItem).get(media_item_id)
                        if mi:
                            mi.local_path = str(local_path)
                            mi.cached_at = datetime.now(timezone.utc)
                            mi.cache_size_bytes = local_path.stat().st_size
                    db_session.commit()
                    
            finally:
                db_session.close()
        
        # Start background task
        task = asyncio.create_task(_download_task())
        self._active_downloads[cache_key] = task
        
        return job_id
    
    def get_storage_usage(self) -> StorageStats:
        """Calculate current cache storage usage"""
        total_size = 0
        
        for root, dirs, files in os.walk(self.cache_path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
        
        limit_bytes = settings.MAX_CACHE_SIZE_GB * (1024**3)
        return StorageStats(total_size, limit_bytes)
    
    async def cleanup_old_cache(self, target_free_gb: float = 10) -> int:
        """
        Remove oldest cached content to free up space.
        Returns number of files deleted.
        """
        stats = self.get_storage_usage()
        target_free_bytes = target_free_gb * (1024**3)
        
        if stats.free_bytes >= target_free_bytes:
            return 0
        
        bytes_to_free = target_free_bytes - stats.free_bytes
        freed = 0
        deleted_count = 0
        
        # Get all cache files with their mtime
        cache_files = []
        for root, dirs, files in os.walk(self.cache_path):
            for f in files:
                fp = Path(root) / f
                try:
                    stat = fp.stat()
                    cache_files.append((fp, stat.st_mtime, stat.st_size))
                except OSError:
                    pass
        
        # Sort by oldest first
        cache_files.sort(key=lambda x: x[1])
        
        for filepath, mtime, size in cache_files:
            if freed >= bytes_to_free:
                break
            
            try:
                filepath.unlink()
                freed += size
                deleted_count += 1
                logger.info(f"Deleted old cache: {filepath.name}")
            except OSError:
                pass
        
        logger.info(f"Cleanup: freed {freed / (1024**3):.2f} GB, deleted {deleted_count} files")
        return deleted_count


# Global instance
cache_service = CacheService()
