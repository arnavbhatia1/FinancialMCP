#!/usr/bin/env python3
"""Start the FinancialMCP server."""

import sys
import os

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from financial_mcp.server import main

if __name__ == "__main__":
    main()
