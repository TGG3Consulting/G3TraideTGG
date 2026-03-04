# Minimal diagnostic
import sys
print(f"Python {sys.version}", flush=True)

print("1. Testing openpyxl...", flush=True)
try:
    import openpyxl
    print("   OK", flush=True)
except Exception as e:
    print(f"   FAILED: {e}", flush=True)

print("2. Testing requests...", flush=True)
try:
    import requests
    print("   OK", flush=True)
except Exception as e:
    print(f"   FAILED: {e}", flush=True)

print("3. Done", flush=True)
