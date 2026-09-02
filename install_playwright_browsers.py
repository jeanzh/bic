import subprocess
import sys

print("Installing Playwright browsers...")
result = subprocess.run(
    [sys.executable, '-m', 'playwright', 'install', 'chromium'],
    capture_output=False,
)
if result.returncode == 0:
    print("Playwright browsers installed successfully.")
else:
    print("Failed to install Playwright browsers.", file=sys.stderr)
    sys.exit(result.returncode)
