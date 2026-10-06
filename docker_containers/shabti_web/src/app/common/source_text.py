from html import escape


def source_text(text: str):
    """A foldout holding a source's chunk, as raw HTML for the chat's markdown to pass through.

    The chunk is document text rather than markdown, so it is escaped and its line breaks kept by
    CSS rather than read as formatting. It is all on one line because a markdown HTML block ends at
    the first blank line, and a chunk's paragraph break would otherwise drop the rest of it out of
    the foldout and into the message as markdown.
    """
    body = "&#10;".join(escape(text).splitlines())
    return f'<details class="shabti-source-text"><summary>Retrieved text</summary><div>{body}</div></details>'
