from app.core import Config, parse
from app.util import slugify


def run_jobs():
    return slugify(parse(Config("jobs").path)[0])
