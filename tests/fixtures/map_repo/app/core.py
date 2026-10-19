"""Core: parse and Config are used by every other module."""


class Config:
    def __init__(self, path):
        self.path = path


def parse(text):
    return text.split()
