import bleach
import markdown as md
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

ALLOWED_TAGS = [
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u", "s", "del",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "a", "table", "thead", "tbody", "tr", "th", "td",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
    "code": ["class"],
}


@register.filter(name="markdownify", is_safe=True)
def markdownify(text):
    if not text:
        return ""
    html = md.markdown(
        text,
        extensions=["extra", "sane_lists", "nl2br"],
    )
    clean_html = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True,
    )
    return mark_safe(clean_html)