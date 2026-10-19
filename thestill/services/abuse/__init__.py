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

"""Spec #104 — abuse prevention and user administration.

Kill switches (account status, signup gate, runtime feature switches),
the follow gate, the audit log and the account-admin actions shared by the
CLI and the admin API. ``wiring.build_abuse_services`` is the one place
the web app, the CLI and the MCP server construct them.

Kept import-light on purpose: ``services/__init__`` pulls in the follower
service, which imports ``abuse.errors``.
"""
