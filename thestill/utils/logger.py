import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logger(
    name: str = "thestill",
    log_file: Optional[str] = None,
    log_level: str = "INFO",
    console_output: bool = True
) -> logging.Logger:
    """Set up logging configuration"""

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if console_output:
        # Use stderr for all logging to keep stdout clean for MCP protocol
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


class ProcessingLogger:
    """Logger specifically for processing operations"""

    def __init__(self, log_file: Optional[str] = None):
        self.logger = setup_logger("thestill.processing", log_file)

    def log_episode_start(self, episode_title: str, podcast_title: str):
        """Log episode processing start"""
        self.logger.info(f"Starting processing: {podcast_title} - {episode_title}")

    def log_episode_complete(self, episode_title: str, processing_time: float):
        """Log episode processing completion"""
        self.logger.info(f"Completed processing: {episode_title} in {processing_time:.1f}s")

    def log_episode_error(self, episode_title: str, error: str):
        """Log episode processing error"""
        self.logger.error(f"Error processing {episode_title}: {error}")

    def log_download_progress(self, filename: str, progress: float):
        """Log download progress"""
        self.logger.debug(f"Download progress {filename}: {progress:.1f}%")

    def log_transcription_start(self, audio_file: str, model: str):
        """Log transcription start"""
        self.logger.info(f"Starting transcription: {audio_file} with model {model}")

    def log_transcription_complete(self, audio_file: str, processing_time: float):
        """Log transcription completion"""
        self.logger.info(f"Transcription complete: {audio_file} in {processing_time:.1f}s")

    def log_llm_processing_start(self, episode_guid: str):
        """Log LLM processing start"""
        self.logger.info(f"Starting LLM processing: {episode_guid}")

    def log_llm_processing_complete(self, episode_guid: str, processing_time: float):
        """Log LLM processing completion"""
        self.logger.info(f"LLM processing complete: {episode_guid} in {processing_time:.1f}s")

    def log_cost_estimate(self, episode_title: str, estimated_cost: float):
        """Log estimated processing cost"""
        self.logger.info(f"Estimated cost for {episode_title}: ${estimated_cost:.4f}")


def get_default_log_path() -> str:
    """Get default log file path"""
    return str(Path("./data/logs/thestill.log"))
