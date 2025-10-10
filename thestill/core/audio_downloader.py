import os
import hashlib
from pathlib import Path
from typing import Optional
import requests
from urllib.parse import urlparse

from ..models.podcast import Episode
from .youtube_downloader import YouTubeDownloader


class AudioDownloader:
    def __init__(self, storage_path: str = "./data/original_audio"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.youtube_downloader = YouTubeDownloader(storage_path)

    def download_episode(self, episode: Episode, podcast_title: str) -> Optional[str]:
        """
        Download episode audio file to original_audio/ directory.

        Returns:
            Path to downloaded audio file, or None if download failed
        """
        try:
            # Check if this is a YouTube URL
            if self.youtube_downloader.is_youtube_url(str(episode.audio_url)):
                return self.youtube_downloader.download_episode(episode, podcast_title)

            # Handle regular audio URLs
            safe_podcast_title = self._sanitize_filename(podcast_title)
            safe_episode_title = self._sanitize_filename(episode.title)

            url_hash = hashlib.md5(str(episode.audio_url).encode()).hexdigest()[:8]

            parsed_url = urlparse(str(episode.audio_url))
            extension = self._get_file_extension(parsed_url.path)

            filename = f"{safe_podcast_title}_{safe_episode_title}_{url_hash}{extension}"
            local_path = self.storage_path / filename

            if local_path.exists():
                print(f"File already exists: {filename}")
                return str(local_path)

            print(f"Downloading: {episode.title}")
            response = requests.get(
                str(episode.audio_url),
                stream=True,
                headers={'User-Agent': 'thestill.ai/1.0'},
                timeout=30
            )
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            print(f"\rProgress: {progress:.1f}%", end='', flush=True)

            print(f"\nDownload completed: {filename}")
            return str(local_path)

        except requests.exceptions.RequestException as e:
            print(f"Network error downloading {episode.title}: {e}")
            return None
        except Exception as e:
            print(f"Error downloading {episode.title}: {e}")
            return None

    def get_file_size(self, file_path: str) -> int:
        """Get file size in bytes"""
        try:
            return os.path.getsize(file_path)
        except:
            return 0

    def cleanup_old_files(self, days: int = 30):
        """Remove audio files older than specified days"""
        import time
        cutoff_time = time.time() - (days * 24 * 60 * 60)

        removed_count = 0
        for file_path in self.storage_path.glob("*"):
            if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                try:
                    file_path.unlink()
                    removed_count += 1
                except Exception as e:
                    print(f"Error removing {file_path}: {e}")

        if removed_count > 0:
            print(f"Cleaned up {removed_count} old audio files")

    def _sanitize_filename(self, filename: str) -> str:
        """Remove/replace invalid filename characters"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')

        filename = filename.replace(' ', '_')
        filename = ''.join(c for c in filename if c.isprintable())

        return filename[:100]

    def _get_file_extension(self, url_path: str) -> str:
        """Extract file extension from URL path"""
        extensions = {'.mp3', '.m4a', '.wav', '.aac', '.ogg', '.flac'}

        for ext in extensions:
            if url_path.lower().endswith(ext):
                return ext

        return '.mp3'