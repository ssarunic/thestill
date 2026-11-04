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

"""``response_text`` decodes HTML pages the way ``Response.text`` does not."""

from typing import Optional

import requests

from thestill.utils.http_text import response_text

_TITLE = "The Risks and Opportunities of an AI Future — with Eric Schmidt"


def _response(body: bytes, content_type: Optional[str]) -> requests.Response:
    resp = requests.Response()
    resp.status_code = 200
    resp._content = body
    if content_type is not None:
        resp.headers["Content-Type"] = content_type
    # What requests' HTTPAdapter.build_response does for a real response.
    resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)
    return resp


class TestResponseText:
    def test_bare_text_html_utf8_body_is_read_as_utf8(self):
        resp = _response(f"<title>{_TITLE}</title>".encode("utf-8"), "text/html")

        # The bug: requests falls back to ISO-8859-1 for text/* without a charset.
        assert "â\x80\x94" in resp.text
        assert response_text(resp) == f"<title>{_TITLE}</title>"

    def test_declared_charset_is_honoured_for_a_non_utf8_body(self):
        resp = _response("<p>Jungestål</p>".encode("iso-8859-1"), "text/html; charset=ISO-8859-1")
        assert response_text(resp) == "<p>Jungestål</p>"

    def test_missing_content_type_header(self):
        resp = _response(_TITLE.encode("utf-8"), None)
        assert response_text(resp) == _TITLE

    def test_undecodable_body_without_charset_still_returns_text(self):
        resp = _response(b"caf\xe9", "text/html")
        assert response_text(resp) == "café"
