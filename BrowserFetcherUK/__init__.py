import configparser
import os


def _version():
    cp = configparser.ConfigParser()
    cp.read(os.path.join(os.path.dirname(__file__), "metadata.txt"), encoding="utf-8")
    return cp.get("general", "version", fallback="?")


__version__ = _version()


def classFactory(iface):
    from .plugin import BrowserFetcherPlugin
    return BrowserFetcherPlugin(iface)
