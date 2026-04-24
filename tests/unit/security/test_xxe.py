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

"""Regression tests for spec #25, item 1.1 — XXE in RSS parsing."""

import pytest

from thestill.core import media_source


class TestMediaSourceXmlParser:
    """media_source.ET must be the defusedxml parser, not stdlib."""

    def test_et_is_defusedxml(self):
        module_name = getattr(media_source.ET, "__name__", "")
        # defusedxml.ElementTree exposes the same API as xml.etree.ElementTree
        # but without external-entity support.
        assert "defusedxml" in module_name, (
            f"Expected defusedxml.ElementTree in media_source, got {module_name!r}. "
            "Plain stdlib ElementTree is vulnerable to XXE / billion-laughs attacks."
        )

    def test_external_entity_rejected(self):
        """A feed referencing an external entity must raise, not silently resolve it."""
        malicious = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE root [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>'
            "<root><title>&xxe;</title></root>"
        )
        with pytest.raises(Exception) as exc_info:
            media_source.ET.fromstring(malicious)
        # defusedxml raises EntitiesForbidden / DTDForbidden / etc. — any of
        # the defusedxml.* family is acceptable. The important guarantee is
        # that the payload is *not* silently expanded into file contents.
        exc_name = type(exc_info.value).__module__ + "." + type(exc_info.value).__name__
        assert "defusedxml" in exc_name.lower() or "forbidden" in exc_name.lower(), exc_name
