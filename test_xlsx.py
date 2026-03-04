# Test which xlsx library works with Python 3.14
import sys
print(f"Python {sys.version}", flush=True)

print("\n1. Testing xlsxwriter...", flush=True)
try:
    import xlsxwriter
    print("   xlsxwriter OK", flush=True)

    # Quick write test
    wb = xlsxwriter.Workbook('test_output.xlsx')
    ws = wb.add_worksheet()
    ws.write(0, 0, 'Test')
    wb.close()
    print("   Write test OK", flush=True)
except Exception as e:
    print(f"   FAILED: {e}", flush=True)
    print("   Install: py -m pip install xlsxwriter", flush=True)

print("\n2. Testing openpyxl...", flush=True)
try:
    import openpyxl
    print("   openpyxl OK", flush=True)
except Exception as e:
    print(f"   FAILED: {e}", flush=True)

print("\nDone", flush=True)
