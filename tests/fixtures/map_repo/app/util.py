"""Util: slugify is used by two modules."""


def slugify(text):
    return text.lower().replace(" ", "-")
