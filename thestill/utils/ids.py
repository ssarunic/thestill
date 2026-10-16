# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Backend-independent id helpers (no database driver imports)."""

import uuid


def is_uuid(value: object) -> bool:
    """True when ``value`` parses as a UUID.

    Ids from URL paths reach ``uuid`` columns unvalidated; on Postgres a
    malformed one raises on the cast instead of matching nothing, so
    callers check the shape first and treat a bad id as a miss.
    """
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True
