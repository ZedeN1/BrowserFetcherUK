import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon

MENU = '&Browser Fetcher UK'


class BrowserFetcherPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        self.action = QAction(QIcon(icon_path), 'Browser Fetcher UK', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToWebMenu(MENU, self.action)

    def unload(self):
        if self.dialog is not None:
            self.dialog.cleanup()
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginWebMenu(MENU, self.action)

    def run(self):
        if self.dialog is None:
            from .dialog import BrowserFetcherDialog
            self.dialog = BrowserFetcherDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.dialog.refresh_status()
