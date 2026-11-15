import logging

import httpx


class AccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            client, method, target, version, status = args
            record.args = (client, method, target.partition("?")[0], version, status)
        return True


access_log_filter = AccessLogFilter()


class AmapLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                arg.copy_with(query=None, fragment=None)
                if isinstance(arg, httpx.URL)
                and arg.host in {"restapi.amap.com", "webapi.amap.com"}
                else arg
                for arg in record.args
            )
        return True


amap_log_filter = AmapLogFilter()
