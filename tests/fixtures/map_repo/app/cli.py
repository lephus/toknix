from app.core import Config, parse
from app.util import slugify


def main():
    cfg = Config("x")
    return [slugify(w) for w in parse(cfg.path)]
