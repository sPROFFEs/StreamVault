
#!/usr/bin/env python3
"""
Workflow Import Script - Ejecutable desde docker compose
Use: docker compose exec core python -m import_workflow <path_to_json>
"""

import sys
import os
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Try to load .env manually if not running in docker
try:
    from dotenv import load_dotenv
    from pathlib import Path
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded environment from {env_path}")
except ImportError:
    logger.warning("python-dotenv not installed. Assuming environment variables are set.")

from processors.workflow_extractor import WorkflowExtractor
from models.base import SessionLocal

def main():
    if len(sys.argv) < 2:
        logger.error("Usage: python import_workflow.py <path_to_json>")
        sys.exit(1)
    
    file_path = sys.argv[1]

    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        sys.exit(1)

    logger.info("Starting workflow import...")
    
    try:
        db = SessionLocal()
        extractor = WorkflowExtractor(db)
        stats = extractor.process_file(file_path, source_name="tmovie.tv")
        
        print("\n" + "="*30)
        print("IMPORT SUMMARY")
        print("="*30)
        print(f"Total Unique Items Found: {stats['found']}")
        print(f"Successfully Added/Linked: {stats['added']}")
        print(f"Skipped (Already Exists): {stats['skipped']}")
        print(f"Errors: {stats['errors']}")
        print("="*30 + "\n")
        
    except Exception as e:
        logger.critical(f"Critical error during execution: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if 'db' in locals():
            db.close()

if __name__ == "__main__":
    main()
