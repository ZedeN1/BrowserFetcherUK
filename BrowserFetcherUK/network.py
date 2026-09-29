"""HTTP requests through the QGIS network stack, so QGIS proxy and SSL settings apply.

Blocking: only call from a QgsTask, where cancel and timeout can interrupt a
request (on the main thread QGIS runs it in a helper thread that ignores both).
"""
import threading
import time

from qgis.core import QgsBlockingNetworkRequest, QgsFeedback
from qgis.PyQt.QtCore import QUrl, QByteArray
from qgis.PyQt.QtNetwork import QNetworkRequest


def request(url, data=None, feedback=None, content_type="application/json", timeout=None,
            headers=None):
    """GET (or POST when data is given) and return the body as bytes.

    Raises RuntimeError on failure, including cancellation: an aborted
    QgsBlockingNetworkRequest reports no error and an empty body.

    timeout (seconds) aborts the request after that long; it works through a
    feedback of its own, since QNetworkRequest.setTransferTimeout is ignored
    under Qt 5.
    """
    req = QNetworkRequest(QUrl(url))
    # Catalogue pages change between runs; never serve them from the QGIS cache.
    req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                     QNetworkRequest.CacheLoadControl.AlwaysNetwork)
    req.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
    for name, value in (headers or {}).items():
        req.setRawHeader(name.encode(), value.encode())
    if data is not None:
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, content_type)

    parent = feedback
    timer = None
    if timeout:
        feedback = QgsFeedback()
        if parent is not None:
            parent.canceled.connect(feedback.cancel)
        timer = threading.Timer(timeout, feedback.cancel)
        timer.start()

    blocking = QgsBlockingNetworkRequest()
    started = time.monotonic()
    try:
        if data is None:
            err = blocking.get(req, True, feedback)
        else:
            err = blocking.post(req, QByteArray(data), True, feedback)
    finally:
        if timer is not None:
            timer.cancel()

    if parent is not None and parent.isCanceled():
        raise RuntimeError("cancelled")
    if feedback is not None and feedback.isCanceled():
        raise RuntimeError(f"no answer within {timeout} s")
    no_answer = f"no answer (connection closed after {time.monotonic() - started:.0f} s)"
    if err != QgsBlockingNetworkRequest.ErrorCode.NoError:
        reply = blocking.reply()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if not bytes(reply.content()) and (not status or 200 <= int(status) < 300):
            # Closed without a reply, or an empty "200 OK": not a real answer either way.
            raise RuntimeError(no_answer)
        if status:
            reason = reply.attribute(QNetworkRequest.Attribute.HttpReasonPhraseAttribute) or ""
            raise RuntimeError(f"HTTP {status} {reason}".strip())
        raise RuntimeError(blocking.errorMessage())
    body = bytes(blocking.reply().content())
    if not body:
        # The server (or something on the way) closed the connection without answering;
        # the DEFRA catalogue does this after exactly 60 s when it is struggling.
        raise RuntimeError(no_answer)
    return body
