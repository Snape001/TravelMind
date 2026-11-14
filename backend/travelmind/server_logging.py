import logging


class AccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            client, method, target, version, status = args
            record.args = (client, method, target.partition("?")[0], version, status)
        return True


access_log_filter = AccessLogFilter()
