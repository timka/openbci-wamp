import sys
import struct
from StringIO import StringIO

from twisted.logger import Logger
from twisted.python import usage
from twisted.internet.protocol import Protocol, Factory
from twisted.protocols.basic import LineReceiver
from twisted.internet import task
from twisted.internet.defer import inlineCallbacks, returnValue, Deferred


from open_bci_v3 import OpenBCIBoard

START_BYTE = b'\xa0'
END_BYTE = b'\xc0'
SAMPLE_RATE = 250.0  # Hz
ADS1299_Vref = 4.5  #reference voltage for ADC in ADS1299.  set by its hardware
ADS1299_gain = 24.0  #assumed gain setting for ADS1299.  set by its Arduino code
scale_fac_uVolts_per_count = ADS1299_Vref/float((pow(2,23)-1))/ADS1299_gain*1000000.
scale_fac_accel_G_per_count = 0.002 /(pow(2,4)) #assume set to +/4G, so 2 mG 

clock = task.Clock()

class OpenBCIProtocol(LineReceiver):
    log = Logger()
    packet_length = 1 + 1 + 24 + 6 + 1
    # delimiter = '\r\n'

    def __init__(self, callback, filter_data=True, scaled_output=True,
        daisy=False, check_interval=2, max_packets_to_skip=10):
        if callable(callback):
            callback = [callback]
        self.callbacks = callback
        self.io = StringIO()
        self.skipped_bytes = 0
        self.streaming = False
        self.reconnecting = False
        self.packet_start = None
        self.check_interval = check_interval
        self.check_task = None
        self.max_packets_to_skip = max_packets_to_skip
        self.initialized = False
        self.setLineMode()

        # Imitate OpenBCIBoard
        self.board = type('MockOBCIBoard', (), {})()
        self.board.ser = type('MockSerial', (), {})()
        self.board.ser.read = self.ser_read
        self.board.ser.write = self.ser_write
        self.board.warn = self.board_warn
        self.board.scaling_output = scaled_output
        self.board.filtering_data = filter_data
        self.board.eeg_channels_per_sample = 8
        self.board.aux_channels_per_sample = 3
        self.board.read_state = 0
        self.board.packets_dropped = 0
        self.board.daisy = daisy

    def ser_read(self, n):
        '''Imitate OpenBCIBoard.ser.read'''
        r = self.ioread.read(n)
        self.log.info('read: {r!r}', r=r)
        return r 

    def ser_write(self, data):
        '''Imitate OpenBCIBoard.ser.write'''
        return self.transport.write(data)

    def board_warn(self, msg):
        '''Imitate OpenBCIBoard.warn()'''
        return self.log.warn(msg)

    def init(self):
        self.initialized = False
        self.transport.write('v')

    def startStreaming(self):
        self.log.info('startStreaming: {d}', d=vars(self))
        if self.initialized and not self.streaming:
            self.streaming = True
            self.transport.write('b')
            self.setRawMode()
            self.setChecker()

    def stopStreaming(self):
        if self.check_task:
            self.check_task.stop()
        self.transport.write('s')
        self.setLineMode()
        self.streaming = False

    def setChecker(self):
        self.check_task = task.LoopingCall(self.checkConnection) 
        self.check_task.start(self.check_interval)

    def checkConnection(self):
        #check number of dropped packages and establish connection problem if
        #too large
        if self.board.packets_dropped > self.max_packets_to_skip:
          #if error, attempt to reconect
          self.reconnect()

    def reconnect(self):
        self.board.packets_dropped = 0
        self.stopStreaming()
        clock.callLater(0.5, self.init)
        clock.callLater(1.0, self.startStreaming)

    def rawDataReceived(self, data):
        # self.log.info('data: {d!r}', d=data)
        if self.streaming:
            self.io.write(data)
            self.readPacket()

    def lineReceived(self, line):
        self.log.info('line: {l}', l=line)
        if not self.initialized and 'Free RAM:' in line.strip():
            self.initialized = True
            self.onReady()

    def onReady(self):
        pass

    def readPacket(self):
        # print >> sys.stderr, 'len(data): {l}'.format(l=len(data))

        # Make sure we got full packet
        buff = self.io.getvalue()
        # self.log.debug('buff: {buff!r}', buff=buff)
        l = len(buff)
        if self.packet_start is None:
            packet_start = buff.find(START_BYTE)
            self.log.debug('packet_start: {s}', s=packet_start)
            packet_started = packet_start >= 0
            if packet_started:
                self.packet_start = packet_start
            else:
                self.skipped_bytes = l

        packet = dict()        
        if self.packet_start is not None:
            packet_end = self.packet_start + self.packet_length
            whole_packet = (packet_end <= l) 
            self.log.debug('packet: {p!r}', p=buff[self.packet_start:packet_end])
            if not whole_packet:
                return

            # if buff[packet_end] != END_BYTE:
            #     self.board.packets_dropped += 1
            #     self.packet_start = None
            #     self.io.truncate(0)
            #     return

            packet_data = buff[self.packet_start + 1:packet_end]
            packet_id = struct.unpack('B', packet_data[0])[0] #packet id goes from 0-255

            channel_data_start = self.packet_start + 1 + 1
            value_len = 3
            channel_data_end = channel_data_start + value_len * self.board.eeg_channels_per_sample
            channel_bytes = buff[channel_data_start:channel_data_end]
            self.log.debug('chan: {c!r}', c=channel_bytes)
            channel_data = []
            for c in range(0, len(channel_bytes), value_len):
                packed = channel_bytes[c:c + value_len]
                self.log.debug('packed chan data: {d!r}', d=packed)
                unpacked = struct.unpack('3B', packed)[0]

                #3byte int in two's complement
                if (unpacked >= 127):
                    pre_fix = '\xFF'
                else:
                    pre_fix = '\x00'

                packed = pre_fix + packed;

                # unpack little endian(>) signed integer(i) (makes unpacking platform
                # independent)
                n = struct.unpack('>i', packed)[0]
                channel_data.append(n)

            value_len = 2
            aux_bytes = buff[channel_data_end:channel_data_end +
                                value_len * self.board.aux_channels_per_sample]
            aux_data = []
            for a in range(0, len(aux_bytes), value_len):
                n = struct.unpack('>h', packet_data[a:a + value_len])[0]
                aux_data.append(n)


            packet['id'] = packet_id
            packet['channel_data'] = channel_data
            packet['aux_data'] = aux_data

        if packet:
            self.skipped_bytes = 0
            self.packet_start = None
            self.io.truncate(0)
            for callback in self.callbacks:
                callback(packet)


class Options(usage.Options):
    optParameters = [
        ['baudrate', 'b', 115200, 'Serial baudrate [default: 115200]'],
        ['port', 'p', '/dev/ttyS0', 'Serial Port device'],
    ]


def OpenBCIFactory(Factory):
    protocol = OpenBCIProtocol


def main():
    from twisted.internet.serialport import SerialPort
    from twisted.internet import reactor
    from twisted.internet.endpoints import TCP4ServerEndpoint

    options = Options()
    try:
        options.parseOptions()
    except usage.UsageError as error:
        sys.stderr.write('%s: %s\n' % (sys.argv[0], error))
        sys.stderr.write('%s: Try --help for usage details.' % (sys.argv[0]))
        sys.exit(1)

    def callback(packet):
        print packet

    baudrate = int(options.opts['baudrate'])
    port = options.opts['port']
    protocol = OpenBCIProtocol(callback)
    s = SerialPort(protocol, port, reactor, baudrate=baudrate)
    endpoint = TCP4ServerEndpoint(reactor, 9876)
    endpoint.listen(OpenBCIFactory)
    reactor.run() 


if __name__ == '__main__':
    main()