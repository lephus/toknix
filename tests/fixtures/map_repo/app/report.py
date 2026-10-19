from app.core import Config, parse


def build_report():
    return parse(Config("report").path)
