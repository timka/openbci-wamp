from twisted.internet.defer import inlineCallbacks
from twisted.logger import Logger
from twisted.internet import reactor

from autobahn.twisted.util import sleep
from autobahn.twisted.wamp import ApplicationSession
from autobahn.wamp.exception import ApplicationError


from twistedobci import OpenBCIProtocol


class AppSession(ApplicationSession):
    log = Logger()
    chunk_event = 'com.example.on_chunk'

    def publish_chunk(self, chunk):
        return self.publish(self.chunk_event, chunk)

    @inlineCallbacks
    def onJoin(self, details):
        port = self.config.extra['port']
        baudrate = self.config.extra['baudrate']
        protocol = OpenBCIProtocol(self.publish_chunk)
        try:
            serial = SerialPort(protocol, port, reactor, baudrate=baudrate)
        except Exception as e:
            self.logger.exception('Could not open serial port')
            self.leave()
 
