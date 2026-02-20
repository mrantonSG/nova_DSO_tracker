# Running Tests in PyCharm

This guide explains how to configure PyCharm to run pytest tests for the Nova DSO Tracker project.

## Quick Setup

### Step 1: Select pytest as Test Runner

1. Open PyCharm and go to **File → Settings** (or **PyCharm → Settings** on macOS)
2. Navigate to **Tools → Python Integrated Tools**
3. Under **Default test runner**, select **pytest**
4. Click **OK** to save

### Step 2: Create a pytest Configuration

1. Right-click on the `tests/` directory in the Project view
2. Select **Run 'pytest in tests'**
3. The test runner will open and run all tests

### Step 3: Create Custom Test Configurations (Optional)

For running specific test files or individual tests:

1. Go to **Run → Edit Configurations...**
2. Click **+** → **pytest**
3. Configure as needed:
   - **Target**: Select "Module path" or "Path" and specify your test file or directory
   - **Additional arguments**: Add pytest options like `-v` (verbose), `-k` (filter), etc.

## Useful pytest Options

Add these to the **Additional arguments** field:

| Option | Description |
|--------|-------------|
| `-v` | Verbose output (show individual test names) |
| `-k test_name` | Run tests matching `test_name` pattern |
| `--tb=short` | Shorter traceback format |
| `-x` | Stop on first failure |
| `-s` | Don't capture output (print statements visible) |

## Example Configurations

### Run All Tests
```
tests/
```

### Run Specific Test File
```
tests/test_framing_persistence.py
```

### Run Specific Test Function
```
tests/test_mosaic_math.py::test_mosaic_3x3_dec_plus_70
```

### Run Tests Matching Pattern
```
-k "framing"
```

## Test Coverage

The current test suite includes:

- **`tests/test_framing_persistence.py`**: Round-trip YAML export/import tests for SavedFraming
- **`tests/test_mosaic_math.py`**: Spherical stepping algorithm validation for mosaic calculations
- **`tests/conftest.py`**: pytest fixtures for in-memory SQLite database

## Running from Terminal

You can also run tests from the terminal:

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_framing_persistence.py -v

# Run specific test
python -m pytest tests/test_mosaic_math.py::test_mosaic_3x3_dec_plus_70 -v

# Run with coverage
python -m pytest tests/ -v --cov=nova --cov-report=html
```

## Troubleshooting

### "pytest is not recognized"
Make sure pytest is installed:
```bash
pip install pytest
```

### Tests fail with import errors
Ensure you're running tests from the project root directory where `nova/` is accessible.

### Database errors during tests
The test suite uses in-memory SQLite (`:memory:`) via fixtures in `conftest.py`. Tests are isolated from production data.

## Debugging Tests

To debug a test in PyCharm:

1. Right-click on the test function in the editor
2. Select **Debug 'test_function_name'**
3. Use standard PyCharm debugging features (breakpoints, stepping, variable inspection)
