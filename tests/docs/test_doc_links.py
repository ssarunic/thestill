"""Every relative markdown link in the docs resolves to a real file.

Catches the ``test_cloud_logging.py`` class of drift: a doc pointing at a
file that was renamed or deleted.
"""

from tests.docs.helpers import iter_doc_files, markdown_links, read_doc

_SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def test_relative_doc_links_resolve():
    broken = []
    for doc in iter_doc_files():
        for target in markdown_links(read_doc(doc)):
            if target.startswith(_SKIP_PREFIXES):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue
            resolved = (doc.parent / path).resolve()
            if not resolved.exists():
                broken.append(f"{doc.name}: [{target}] -> {resolved}")
    assert not broken, "Broken relative links in docs:\n" + "\n".join(broken)
