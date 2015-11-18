import sys
# reload(sys).setdefaultencoding('utf-8')

from twisted.internet.defer import inlineCallbacks
from twisted.logger import Logger
from twisted.internet import reactor
from twisted.internet.serialport import SerialPort


from autobahn.twisted.wamp import ApplicationSession
from autobahn.wamp.exception import ApplicationError

from twistedobci import OpenBCIProtocol


class OpenBCIComponent(ApplicationSession):
    log = Logger()
    serial = None
    chunk_size = None

    class events:
        _prefix = u'com.example.'
        chunk = _prefix + u'on_chunk'
        ready = _prefix + u'ready'
        start_streaming = _prefix + u'start_streaming'
        stop_streaming = _prefix + u'stop_streaming'
        init = _prefix + u'init'

    def onPacket(self, packet):
        if not packet:
            return
        self.packets.append(packet)
        if len(self.packets) == self.chunk_size:
            self.publish(self.events.chunk, self.packets)
            self.packets = []

    def onReady(self):
        self.log.info('ready')
        self.publish(self.events.ready)
        self.log.info('startStreaming')
        self.protocol.startStreaming()

    @inlineCallbacks
    def onJoin(self, details):
        port = self.config.extra['port']
        self.chunk_size = self.config.extra.get('chunk', 125)
        baudrate = self.config.extra.get('baudrate', 115200)
        self.protocol = protocol = OpenBCIProtocol(self.onPacket)
        protocol.log = self.log
        protocol.onReady = self.onReady
        self.packets = []
        try:
            self.serial = SerialPort(protocol, port, reactor, baudrate=baudrate)
        except Exception as e:
            self.log.error('Could not open serial port: ' + str(e))
            self.leave()
        else:
            self.log.info('Openned serial port: %s at %s' % (port, baudrate))
            self.protocol.init()
            self.log.info('init() called')
            yield self.register(self.onReady, self.events.ready)
            yield self.register(protocol.startStreaming,
                self.events.start_streaming)
            yield self.register(protocol.stopStreaming,
                self.events.stop_streaming)

    @inlineCallbacks
    def onLeave(self, details):
        yield self.protocol.stopStreaming()
