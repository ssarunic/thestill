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

"""Spec #104 — who performed an admin action, for the audit log."""

from dataclasses import dataclass
from typing import Literal, Optional

ActorKind = Literal["web", "cli", "system"]


@dataclass(frozen=True)
class Actor:
    """``web`` carries the admin's user id; ``cli`` has none; ``system`` is
    automation (the Phase 3 spend breaker) and names itself."""

    kind: ActorKind
    user_id: Optional[str] = None
    name: Optional[str] = None

    @classmethod
    def web(cls, user_id: str) -> "Actor":
        return cls("web", user_id=user_id)

    @classmethod
    def cli(cls) -> "Actor":
        return cls("cli")

    @classmethod
    def system(cls, name: str) -> "Actor":
        return cls("system", name=name)
