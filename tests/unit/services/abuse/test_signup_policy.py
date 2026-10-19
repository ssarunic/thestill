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

"""Spec #104 §1.2 — the signup gate."""

import pytest

from thestill.services.abuse.actor import Actor
from thestill.services.abuse.errors import SignupRefusedError
from thestill.services.abuse.signup_policy import evaluate_signup


@pytest.mark.parametrize(
    "mode, allowlist, email, allowed",
    [
        ("open", [], "anyone@example.com", True),
        ("closed", ["anyone@example.com"], "anyone@example.com", False),
        ("allowlist", ["alice@example.com"], "Alice@Example.COM", True),
        ("allowlist", ["alice@example.com"], "bob@example.com", False),
        ("allowlist", ["@example.com"], "bob@EXAMPLE.com", True),
        ("allowlist", ["@example.com"], "bob@sub.example.com", False),
        ("allowlist", ["@Corp.io"], "x@corp.io", True),
        ("allowlist", [], "x@corp.io", False),
    ],
)
def test_evaluate_signup(mode, allowlist, email, allowed):
    assert evaluate_signup(mode, allowlist, email) is allowed


def test_policy_reads_runtime_settings(world):
    world.abuse.signup.check("new@example.com")  # open by default
    world.abuse.settings.set("signup_mode", "allowlist", actor=Actor.cli())
    world.abuse.settings.set("signup_allowlist", ["@friends.org"], actor=Actor.cli())
    world.abuse.signup.check("pal@friends.org")
    with pytest.raises(SignupRefusedError):
        world.abuse.signup.check("stranger@example.com")
