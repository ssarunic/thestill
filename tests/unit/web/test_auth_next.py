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

"""Spec #94 — the sign-in return path must never become an open redirect."""

import pytest

from thestill.web.routes.auth import safe_next_path


@pytest.mark.parametrize(
    "value",
    [
        "/",
        "/podcasts/prof-g/episodes/storytelling",
        "/entities/person/scott-galloway?about=company:nvidia",
        "/podcasts/x/episodes/y?view=transcript#t",
    ],
)
def test_same_origin_paths_are_kept(value):
    assert safe_next_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "https://evil.example/",
        "//evil.example/path",
        "/\\evil.example",
        "javascript:alert(1)",
        "podcasts/x",
        "/login",
        "/login?next=/x",
        "/api/auth/google/login",
        "/x\nSet-Cookie: a=b",
        "/" + "a" * 600,
    ],
)
def test_anything_else_is_dropped(value):
    assert safe_next_path(value) is None
