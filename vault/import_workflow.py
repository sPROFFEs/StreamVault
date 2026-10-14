
import sys
import os
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add vault to path to allow imports
current_dir = Path(__file__).parent.absolute()
if str(current_dir) not in sys.path:
    sys.path.append(str(current_dir))

# Try to load .env manually if not running in docker
try:
    from dotenv import load_dotenv
    env_path = current_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded environment from {env_path}")
    else:
        logger.warning(f".env file not found at {env_path}")
except ImportError:
    logger.warning("python-dotenv not installed. Assuming environment variables are set.")

try:
    from core.processors.workflow_extractor import WorkflowExtractor
    from core.models.base import SessionLocal
except ImportError as e:
    import traceback
    traceback.print_exc()
    logger.error(f"Import Error: {e}")
    sys.exit(1)

def main():
    if len(sys.argv) < 2:
        # Default to the known file if not provided
        default_file = Path(r"C:\Users\user\Documents\Stream\workflows\tmovies.json")
        if default_file.exists():
            file_path = str(default_file)
            logger.info(f"No file specified. Using default: {file_path}")
        else:
            logger.error("Usage: python import_workflow.py <path_to_json>")
            sys.exit(1)
    else:
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
