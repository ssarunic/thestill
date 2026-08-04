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

"""Pure-logic tests for the deploy/aws-ec2/thestill-aws provisioning CLI.

Nothing here touches AWS. The point is the parts that are expensive to get
wrong remotely: a bootstrap script with a shell syntax error only reveals
itself after an instance is already running and billing, and a
non-idempotent step only bites on the second run.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "aws-ec2" / "thestill-aws"

pytest.importorskip("boto3", reason="thestill-aws imports boto3 at module level")


def _load_module():
    """Import the extension-less CLI as a module.

    It must be registered in sys.modules before exec_module or @dataclass
    cannot resolve its own module namespace.
    """
    loader = importlib.machinery.SourceFileLoader("thestill_aws_cli", str(SCRIPT))
    spec = importlib.util.spec_from_loader("thestill_aws_cli", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["thestill_aws_cli"] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli():
    return _load_module()


@pytest.fixture(scope="module")
def user_data(cli) -> str:
    return cli.build_user_data("eu-west-2", "thestill-backups-123456789012", "prod-latest")


class TestScriptFile:
    def test_is_executable(self):
        assert SCRIPT.is_file()
        assert SCRIPT.stat().st_mode & 0o111, "thestill-aws must be chmod +x"

    def test_every_subcommand_is_wired(self, cli):
        parser = cli.build_parser()
        actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        commands = set(actions[0].choices)
        assert commands == {
            "setup",
            "secrets",
            "launch",
            "dns",
            "reconcile",
            "status",
            "logs",
            "ssh",
            "teardown",
        }


class TestUserData:
    def test_is_valid_bash(self, user_data, tmp_path):
        script = tmp_path / "user-data.sh"
        script.write_text(user_data)
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"generated bootstrap is not valid bash:\n{result.stderr}"

    @pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
    def test_passes_shellcheck(self, user_data, tmp_path):
        script = tmp_path / "user-data.sh"
        script.write_text(user_data)
        result = subprocess.run(["shellcheck", "-S", "error", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout

    def test_fits_in_the_ec2_user_data_limit(self, user_data):
        # EC2 rejects user-data above 16 KB (base64-encoded).
        assert len(user_data.encode()) < 16384

    def test_sets_strict_mode_and_logs(self, user_data):
        assert user_data.startswith("#!/bin/bash\nset -euo pipefail\n")
        assert "/var/log/user-data.log" in user_data

    def test_interpolated_values_are_shell_quoted(self, cli):
        # A tag with a shell metacharacter must not break out of the
        # assignment into a command.
        hostile = cli.build_user_data("eu-west-2", "bucket", "x'; touch /tmp/pwned; '")
        assert "touch /tmp/pwned" not in hostile.split("\n\n")[0] or "'\"'\"'" in hostile

    def test_hostile_tag_still_parses_as_bash(self, cli, tmp_path):
        hostile = cli.build_user_data("eu-west-2", "bucket", "x'; touch /tmp/pwned; '")
        script = tmp_path / "hostile.sh"
        script.write_text(hostile)
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_data_dir_is_owned_by_the_container_uid(self, user_data, cli):
        # The container runs non-root; a root-owned bind mount breaks every write.
        assert f'install -d -o "$CONTAINER_UID"' in user_data
        assert f"CONTAINER_UID={cli.CONTAINER_UID}" in user_data

    def test_backup_uses_a_systemd_timer_not_cron(self, user_data):
        # AL2023 minimal ships no cron daemon.
        assert "thestill-backup.timer" in user_data
        assert "systemctl enable --now thestill-backup.timer" in user_data
        assert "crontab" not in user_data

    def test_writes_the_bootstrap_completion_marker_last(self, user_data, cli):
        # `status` keys off this file, so it must only be written once the
        # containers are actually up — otherwise a half-bootstrapped box
        # reports "complete". The body writes it via "$BOOTSTRAP_MARKER";
        # the literal path only appears in the variables block.
        assert f"BOOTSTRAP_MARKER={cli.BOOTSTRAP_MARKER}" in user_data
        write = '> "$BOOTSTRAP_MARKER"'
        assert write in user_data
        assert user_data.index(write) > user_data.rindex("docker compose -f docker-compose.prod.yml up -d")


class TestUserDataIdempotency:
    """The same script runs at first boot AND on every `reconcile`."""

    def test_image_tag_is_replaced_not_appended(self, user_data):
        # A bare `>>` would accumulate duplicate assignments on re-run.
        assert 'sed -i "/^THESTILL_IMAGE_TAG=/d" .env' in user_data

    def test_directory_creation_is_convergent(self, user_data):
        # `install -d` succeeds on an existing directory; `mkdir` without -p
        # would abort the whole script under `set -e`.
        assert "mkdir " not in user_data.replace("mkdir -p", "")

    def test_no_step_assumes_a_clean_box(self, user_data):
        for convergent in (
            "dnf install -y",
            "systemctl enable --now docker",
            "docker compose -f docker-compose.prod.yml up -d",
        ):
            assert convergent in user_data


class TestAliasDomains:
    """Option A multi-domain: one canonical origin, N aliases that 301 to it.

    Only the canonical domain is ever served, so sessions, OAuth callbacks
    and email links stay single-origin however many aliases exist.
    """

    @pytest.fixture(scope="class")
    def with_aliases(self, cli) -> str:
        return cli.build_user_data(
            "eu-west-2", "bkt", "prod-latest", "main", "thestill.ai", ["thestill.me", "www.thestill.ai"]
        )

    def test_aliases_are_joined_as_caddy_site_addresses(self, with_aliases):
        assert "REDIRECT_DOMAINS='thestill.me, www.thestill.ai'" in with_aliases
        assert "CANONICAL_DOMAIN=thestill.ai" in with_aliases

    def test_generates_a_permanent_redirect_to_the_canonical_domain(self, with_aliases):
        assert "redir https://%s{uri} permanent" in with_aliases
        assert "> redirects.caddy" in with_aliases

    def test_redirects_file_is_written_even_with_no_aliases(self, cli):
        # The Caddyfile imports it unconditionally; a missing file is a hard
        # parse error, so the empty case must still create it.
        plain = cli.build_user_data("eu-west-2", "bkt", "prod-latest", "main", "thestill.ai", [])
        assert "REDIRECT_DOMAINS=''" in plain
        assert ": > redirects.caddy" in plain

    def test_redirects_file_is_created_before_compose_up(self, with_aliases):
        # Compose would create a *directory* at the bind-mount path otherwise.
        assert with_aliases.index("redirects.caddy") < with_aliases.index(
            "docker compose -f docker-compose.prod.yml up -d"
        )

    def test_caddyfile_imports_the_redirects_file(self):
        caddyfile = (SCRIPT.parent / "Caddyfile").read_text()
        assert "import /etc/caddy/redirects.caddy" in caddyfile

    def test_compose_mounts_the_redirects_file(self):
        compose = (SCRIPT.parent / "docker-compose.prod.yml").read_text()
        assert "./redirects.caddy:/etc/caddy/redirects.caddy:ro" in compose

    def test_setup_accepts_repeatable_aliases(self, cli):
        args = cli.build_parser().parse_args(
            ["setup", "--domain", "thestill.ai", "--alias", "thestill.me", "--alias", "thestill.dev"]
        )
        assert args.alias == ["thestill.me", "thestill.dev"]

    def test_setup_defaults_to_no_aliases(self, cli):
        args = cli.build_parser().parse_args(["setup", "--domain", "thestill.ai"])
        assert args.alias == []


class TestElasticIP:
    """Without a stable address, stop/start or an instance-type change hands
    the box a new IP and silently breaks DNS — which is what makes the
    'resize to 8 cores for a batch job' pattern unsafe."""

    def test_state_carries_the_allocation(self, cli):
        assert "eip_allocation_id" in {f.name for f in __import__("dataclasses").fields(cli.Deployment)}

    def test_launch_exposes_an_opt_out(self, cli):
        args = cli.build_parser().parse_args(["launch"])
        assert args.no_eip is False
        assert cli.build_parser().parse_args(["launch", "--no-eip"]).no_eip is True

    def test_teardown_releases_the_address(self):
        # An allocated-but-unattached EIP keeps billing after teardown.
        src = SCRIPT.read_text()
        assert "release_address" in src, "teardown must release the Elastic IP"

    def test_association_allows_reassociation(self):
        # Retrofitting onto an already-running instance needs this.
        assert "AllowReassociation=True" in SCRIPT.read_text()


class TestReadinessProbe:
    def test_probes_inside_the_container_not_the_host(self):
        # Only Caddy publishes ports; the app's 8000 is compose-internal, so
        # curling the host's localhost:8000 always fails.
        src = SCRIPT.read_text()
        assert "docker compose -f docker-compose.prod.yml exec -T thestill" in src
        assert "curl -fsS -o /dev/null -w '%{http_code}' http://localhost:8000" not in src


class TestOAuthCallbackPath:
    def test_help_text_matches_the_route_the_app_registers(self):
        # auth router mounts at /api/auth, route is /google/callback.
        src = SCRIPT.read_text()
        assert "/api/auth/google/callback" in src
        assert "/api/auth/callback" not in src.replace("/api/auth/google/callback", "")


class TestDeployKitPinning:
    """A mutable 'main' ref would let reconcile pull a compose file newer
    than the image the box runs — silent version skew."""

    def test_kit_ref_is_interpolated_into_the_fetch_url(self, cli):
        sha = "a" * 40
        assert f"/{sha}/deploy/aws-ec2" in cli.build_user_data("eu-west-2", "b", "prod-latest", sha)

    def test_default_ref_is_this_checkout_commit_when_pushed(self, cli):
        ref = cli.default_kit_ref()
        # Either a real 40-char SHA (checkout on origin/main) or the
        # documented 'main' fallback — never anything else.
        assert ref == "main" or (len(ref) == 40 and all(c in "0123456789abcdef" for c in ref))

    def test_launch_and_reconcile_both_accept_kit_ref(self, cli):
        parser = cli.build_parser()
        for command in ("launch", "reconcile"):
            args = parser.parse_args([command, "--kit-ref", "deadbeef"])
            assert args.kit_ref == "deadbeef"


class TestParseEnvFile:
    def test_strips_inline_comments_from_unquoted_values(self, cli, tmp_path):
        # The bug this guards: shipping "abc123  # generated with ..." to SSM
        # as the actual secret, which only fails much later as an auth error.
        path = tmp_path / "s.env"
        path.write_text("JWT_SECRET_KEY=abc123  # generated with openssl\n")
        assert cli.parse_env_file(path) == {"JWT_SECRET_KEY": "abc123"}

    def test_keeps_a_hash_that_is_part_of_the_value(self, cli, tmp_path):
        path = tmp_path / "s.env"
        path.write_text("API_KEY=abc#def\nQUOTED='has # hash'\n")
        assert cli.parse_env_file(path) == {"API_KEY": "abc#def", "QUOTED": "has # hash"}

    def test_reports_every_skipped_line(self, cli, tmp_path, capsys):
        path = tmp_path / "s.env"
        path.write_text(
            "\n".join(
                [
                    "GOOD=value",
                    "not an assignment",
                    "lowercase=oops",
                    "EMPTY=",
                    "UNTERMINATED='oops",
                ]
            )
        )
        assert cli.parse_env_file(path) == {"GOOD": "value"}
        out = capsys.readouterr().out
        for expected in ("not a KEY=VALUE", "upper-case", "empty value", "unterminated"):
            assert expected in out, f"skipped line not reported: {expected}"

    def test_parses_plain_and_quoted_values(self, cli, tmp_path):
        path = tmp_path / "secrets.env"
        path.write_text(
            "\n".join(
                [
                    "# a comment",
                    "",
                    "PLAIN=abc123",
                    "SINGLE='has spaces'",
                    'DOUBLE="has=equals"',
                    "export EXPORTED=works",
                    "  SPACED = padded  ",
                    "lowercase=ignored",
                    "EMPTY=",
                ]
            )
        )
        values = cli.parse_env_file(path)
        assert values == {
            "PLAIN": "abc123",
            "SINGLE": "has spaces",
            "DOUBLE": "has=equals",
            "EXPORTED": "works",
            "SPACED": "padded",
        }

    def test_ignores_empty_values_so_they_never_overwrite_a_real_secret(self, cli, tmp_path):
        path = tmp_path / "s.env"
        path.write_text("JWT_SECRET_KEY=\n")
        assert cli.parse_env_file(path) == {}


class TestConstants:
    def test_ssm_prefix_matches_the_fetch_secrets_default(self, cli):
        fetch = (SCRIPT.parent / "fetch-secrets.sh").read_text()
        assert f"SSM_PREFIX:-{cli.SSM_PREFIX}" in fetch

    def test_required_secrets_cover_the_compose_hard_requirements(self, cli):
        compose = (SCRIPT.parent / "docker-compose.prod.yml").read_text()
        for name in ("POSTGRES_PASSWORD", "PUBLIC_DOMAIN"):
            assert name in compose
            assert name in cli.REQUIRED_SECRETS

    def test_image_repo_matches_the_ci_publish_target(self, cli):
        workflow = (SCRIPT.parents[2] / ".github" / "workflows" / "ci.yml").read_text()
        assert cli.DEFAULT_IMAGE_TAG in workflow, "CI must publish the tag the launcher pulls"
