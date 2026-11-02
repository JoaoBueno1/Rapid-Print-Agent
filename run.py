"""Dev launcher: `python run.py --console`. PyInstaller entry point too."""
import sys

from rapid_print_agent.main import main

if __name__ == "__main__":
    sys.exit(main())
