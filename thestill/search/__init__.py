# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Spec #28 — search/citation helpers shared by MCP, REST, and CLI surfaces."""

from .citation import build_citation_row, build_citation_rows

__all__ = ["build_citation_row", "build_citation_rows"]
