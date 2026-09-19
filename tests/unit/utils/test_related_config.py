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

"""Spec #56 — related-episodes knobs."""

from thestill.utils.config import get_related_incremental_pool_k


def test_pool_k_default_and_override(monkeypatch):
    monkeypatch.delenv("RELATED_INCREMENTAL_POOL_K", raising=False)
    assert get_related_incremental_pool_k() == 150
    monkeypatch.setenv("RELATED_INCREMENTAL_POOL_K", "40")
    assert get_related_incremental_pool_k() == 40
    monkeypatch.setenv("RELATED_INCREMENTAL_POOL_K", "0")
    assert get_related_incremental_pool_k() == 1  # never a zero pool
